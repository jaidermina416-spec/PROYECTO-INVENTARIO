"""
═══════════════════════════════════════════════════════════════
  Proyecto Programación — Sistema de Inventario
  Backend: Flask + PostgreSQL
  Archivo: app.py
═══════════════════════════════════════════════════════════════

INSTALACIÓN:
  pip install -r requirements.txt

CONFIGURACIÓN:
  1. Crea un archivo .env con tus credenciales de PostgreSQL
  2. Ejecuta el schema.sql en tu base de datos
  3. Corre con: python app.py

VARIABLES DE ENTORNO (.env):
  DB_HOST=localhost
  DB_PORT=5432
  DB_NAME=inventario_db
  DB_USER=postgres
  DB_PASSWORD=tu_password
  JWT_SECRET=tu_clave_secreta_muy_larga
  PORT=5000
"""

import os, hashlib, json
from datetime import datetime, timedelta
from functools import wraps
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify, g
from flask_cors import CORS
import jwt as pyjwt

# ── Configuración ────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, origins="*")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST",     "localhost"),
    "port":     int(os.environ.get("DB_PORT", 5432)),
    "dbname":   os.environ.get("DB_NAME",     "inventario_db"),
    "user":     os.environ.get("DB_USER",     "postgres"),
    "password": os.environ.get("DB_PASSWORD", "0912"),
}
JWT_SECRET  = os.environ.get("JWT_SECRET", "mi_clave_super_secreta_cambiar_en_produccion")
JWT_EXPIRES = timedelta(hours=8)

# ── DB Helper ────────────────────────────────────────────────────
def get_db():
    if not hasattr(g, "db"):
        g.db = psycopg2.connect(**DB_CONFIG)
    return g.db

@app.teardown_appcontext
def close_db(error):
    if hasattr(g, "db"):
        g.db.close()

def query(sql, params=None, fetchone=False, fetchall=False, commit=False):
    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or ())
    result = None
    if fetchone:  result = cur.fetchone()
    if fetchall:  result = cur.fetchall()
    if commit:    conn.commit()
    cur.close()
    return result

# ── Auth ─────────────────────────────────────────────────────────
def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def make_token(user):
    payload = {
        "sub":    user["id"],
        "user":   user["usuario"],
        "nombre": user["nombre"],
        "rol":    user["rol"],
        "exp":    datetime.utcnow() + JWT_EXPIRES,
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")

def require_auth(roles=None):
    """Decorador: exige token JWT válido. roles=['admin','operador',...]"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return jsonify({"error": "Token requerido"}), 401
            token = auth[7:]
            try:
                payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            except pyjwt.ExpiredSignatureError:
                return jsonify({"error": "Token expirado"}), 401
            except Exception:
                return jsonify({"error": "Token inválido"}), 401
            if roles and payload.get("rol") not in roles:
                return jsonify({"error": "Sin permisos suficientes"}), 403
            g.current_user = payload
            return f(*args, **kwargs)
        return wrapper
    return decorator

def log_action(tipo, detalle, producto_nombre=None, producto_codigo=None, usuario=None):
    try:
        query(
            """INSERT INTO actividad (tipo, detalle, producto_nombre, producto_codigo, usuario)
               VALUES (%s, %s, %s, %s, %s)""",
            (tipo, detalle, producto_nombre, producto_codigo, usuario or getattr(g, 'current_user', {}).get('user', 'sistema')),
            commit=True
        )
    except Exception:
        pass

# ── ENDPOINT: Ping ───────────────────────────────────────────────
@app.route("/api/ping")
def ping():
    return jsonify({"status": "ok", "message": "Sistema de Inventario — Backend activo"})

# ── ENDPOINT: Auth ───────────────────────────────────────────────
@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Datos requeridos"}), 400
    usuario  = data.get("usuario", "").strip()
    password = data.get("password", "")
    if not usuario or not password:
        return jsonify({"error": "Usuario y contraseña requeridos"}), 400

    user = query(
        "SELECT * FROM usuarios WHERE usuario=%s AND activo=true",
        (usuario,), fetchone=True
    )
    if not user or user["password_hash"] != hash_password(password):
        log_action("login-fail", f"Intento fallido para usuario: {usuario}", usuario=usuario)
        return jsonify({"error": "Credenciales incorrectas"}), 401

    token = make_token(user)
    log_action("login", f"Inicio de sesión exitoso", usuario=usuario)
    return jsonify({
        "token":   token,
        "usuario": user["usuario"],
        "nombre":  user["nombre"],
        "rol":     user["rol"],
    })

@app.route("/api/auth/logout", methods=["POST"])
@require_auth()
def logout():
    log_action("logout", "Cierre de sesión")
    return jsonify({"message": "Sesión cerrada"})

# ── ENDPOINT: Productos ──────────────────────────────────────────
@app.route("/api/productos", methods=["GET"])
@require_auth()
def get_productos():
    orden = request.args.get("orden", "valor_total")
    anio_f = request.args.get("anio")
    mes_f  = request.args.get("mes")
    filtro = {
        "valor_total": "cantidad * precio DESC",
        "precio_asc":  "precio ASC",
        "precio_desc": "precio DESC",
        "cant_asc":    "cantidad ASC",
        "cant_desc":   "cantidad DESC",
        "nombre":      "nombre ASC",
    }.get(orden, "cantidad * precio DESC")

    where_clauses = []
    params = []
    if anio_f:
        where_clauses.append("anio = %s")
        params.append(int(anio_f))
    if mes_f:
        where_clauses.append("mes = %s")
        params.append(mes_f)
    dia_f  = request.args.get("dia")
    if dia_f:
        where_clauses.append("dia = %s")
        params.append(int(dia_f))

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    productos = query(
        f"SELECT *, (cantidad * precio) AS valor_total FROM productos{where_sql} ORDER BY {filtro}",
        params if params else None,
        fetchall=True
    )
    log_action("listar", f"Consulta de inventario ({len(productos)} productos)")
    return jsonify([dict(p) for p in productos])

@app.route("/api/productos", methods=["POST"])
@require_auth(roles=["admin", "operador"])
def crear_producto():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Datos requeridos"}), 400

    nombre   = data.get("nombre", "").strip()
    codigo   = data.get("codigo", "").strip().upper()
    tipo     = data.get("tipo",   "Otro").strip()
    cantidad = int(data.get("cantidad", 0))
    precio   = float(data.get("precio",   0))
    minimo   = int(data.get("minimo",   5))
    anio     = data.get("anio")
    anio     = int(anio) if anio else None
    mes      = data.get("mes", "").strip() or None
    dia_raw  = data.get("dia")
    dia      = int(dia_raw) if dia_raw else None

    if not nombre or not codigo:
        return jsonify({"error": "Nombre y código son requeridos"}), 400

    existente = query("SELECT id FROM productos WHERE codigo=%s", (codigo,), fetchone=True)
    if existente:
        return jsonify({"error": f"El código {codigo} ya existe"}), 409

    query(
        """INSERT INTO productos (nombre, codigo, tipo, cantidad, precio, stock_minimo, anio, mes, dia)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (nombre, codigo, tipo, cantidad, precio, minimo, anio, mes, dia), commit=True
    )
    prod = query("SELECT * FROM productos WHERE codigo=%s", (codigo,), fetchone=True)
    log_action("agregar", f"Producto registrado: {nombre}", nombre, codigo)
    return jsonify(dict(prod)), 201

@app.route("/api/productos/<int:prod_id>", methods=["GET"])
@require_auth()
def get_producto(prod_id):
    prod = query("SELECT * FROM productos WHERE id=%s", (prod_id,), fetchone=True)
    if not prod:
        return jsonify({"error": "Producto no encontrado"}), 404
    return jsonify(dict(prod))

@app.route("/api/productos/<int:prod_id>", methods=["PUT"])
@require_auth(roles=["admin", "operador"])
def actualizar_producto(prod_id):
    data = request.get_json()
    prod = query("SELECT * FROM productos WHERE id=%s", (prod_id,), fetchone=True)
    if not prod:
        return jsonify({"error": "Producto no encontrado"}), 404

    nombre   = data.get("nombre",   prod["nombre"])
    tipo     = data.get("tipo",     prod["tipo"])
    cantidad = data.get("cantidad", prod["cantidad"])
    precio   = data.get("precio",   prod["precio"])
    minimo   = data.get("minimo",   prod["stock_minimo"])
    anio_raw = data.get("anio",   prod["anio"])
    anio     = int(anio_raw) if anio_raw else None
    mes      = data.get("mes",    prod["mes"]) or None
    dia_raw  = data.get("dia",    prod["dia"])
    dia      = int(dia_raw) if dia_raw else None

    query(
        """UPDATE productos SET nombre=%s, tipo=%s, cantidad=%s, precio=%s,
           stock_minimo=%s, anio=%s, mes=%s, dia=%s, actualizado=NOW() WHERE id=%s""",
        (nombre, tipo, cantidad, precio, minimo, anio, mes, dia, prod_id), commit=True
    )
    log_action("editar", f"Producto actualizado: {nombre}", nombre, prod["codigo"])
    prod_actualizado = query("SELECT * FROM productos WHERE id=%s", (prod_id,), fetchone=True)
    return jsonify(dict(prod_actualizado))

@app.route("/api/productos/<int:prod_id>", methods=["DELETE"])
@require_auth(roles=["admin", "operador"])
def eliminar_producto(prod_id):
    prod = query("SELECT * FROM productos WHERE id=%s", (prod_id,), fetchone=True)
    if not prod:
        return jsonify({"error": "Producto no encontrado"}), 404
    query("DELETE FROM productos WHERE id=%s", (prod_id,), commit=True)
    log_action("eliminar", f"Producto eliminado: {prod['nombre']}", prod["nombre"], prod["codigo"])
    return jsonify({"message": f"Producto {prod['nombre']} eliminado"})

# ── ENDPOINT: Alertas ────────────────────────────────────────────
@app.route("/api/alertas")
@require_auth()
def get_alertas():
    alertas = query(
        """SELECT *, (cantidad * precio) AS valor_total
           FROM productos WHERE cantidad <= stock_minimo
           ORDER BY cantidad ASC""",
        fetchall=True
    )
    return jsonify({
        "total":     len(alertas),
        "sin_stock": len([a for a in alertas if a["cantidad"] == 0]),
        "bajo_stock":len([a for a in alertas if 0 < a["cantidad"] <= a["stock_minimo"]]),
        "alertas":   [dict(a) for a in alertas]
    })

# ── ENDPOINT: Actividad ──────────────────────────────────────────
@app.route("/api/actividad")
@require_auth(roles=["admin", "operador"])
def get_actividad():
    limite = int(request.args.get("limite", 200))
    tipo   = request.args.get("tipo")
    fecha  = request.args.get("fecha")

    sql    = "SELECT * FROM actividad"
    params = []
    where  = []
    if tipo:  where.append("tipo=%s");  params.append(tipo)
    if fecha: where.append("DATE(fecha)=%s"); params.append(fecha)
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY fecha DESC LIMIT %s"
    params.append(limite)

    registros = query(sql, params, fetchall=True)
    return jsonify([{**dict(r), "fecha": r["fecha"].isoformat()} for r in registros])

# ── ENDPOINT: Estadísticas ───────────────────────────────────────
@app.route("/api/estadisticas")
@require_auth()
def get_estadisticas():
    stats = query(
        """SELECT
             COUNT(*)                              AS total_productos,
             SUM(cantidad)                         AS total_unidades,
             SUM(cantidad * precio)                AS valor_total,
             AVG(precio)                           AS precio_promedio,
             MAX(precio)                           AS precio_max,
             MIN(CASE WHEN precio>0 THEN precio END) AS precio_min,
             COUNT(CASE WHEN cantidad=0 THEN 1 END) AS sin_stock,
             COUNT(CASE WHEN cantidad<=stock_minimo THEN 1 END) AS bajo_stock
           FROM productos""",
        fetchone=True
    )
    por_tipo = query(
        """SELECT tipo, COUNT(*) AS cantidad_productos,
                  SUM(cantidad * precio) AS valor_total
           FROM productos GROUP BY tipo ORDER BY valor_total DESC""",
        fetchall=True
    )
    return jsonify({
        "resumen":  dict(stats),
        "por_tipo": [dict(t) for t in por_tipo]
    })

# ── ENDPOINT: Usuarios ───────────────────────────────────────────
@app.route("/api/usuarios")
@require_auth(roles=["admin"])
def get_usuarios():
    usuarios = query(
        "SELECT id, nombre, usuario, rol, activo, creado FROM usuarios ORDER BY creado",
        fetchall=True
    )
    return jsonify([{**dict(u), "creado": u["creado"].isoformat()} for u in usuarios])

@app.route("/api/usuarios", methods=["POST"])
@require_auth(roles=["admin"])
def crear_usuario():
    data = request.get_json()
    nombre   = data.get("nombre", "").strip()
    usuario  = data.get("usuario", "").strip().lower()
    password = data.get("password", "")
    rol      = data.get("rol", "viewer")

    if not nombre or not usuario or not password:
        return jsonify({"error": "Todos los campos son requeridos"}), 400
    if len(password) < 4:
        return jsonify({"error": "Contraseña mínimo 4 caracteres"}), 400
    if rol not in ("admin", "operador", "viewer"):
        return jsonify({"error": "Rol inválido"}), 400

    existente = query("SELECT id FROM usuarios WHERE usuario=%s", (usuario,), fetchone=True)
    if existente:
        return jsonify({"error": f"El usuario '{usuario}' ya existe"}), 409

    query(
        "INSERT INTO usuarios (nombre, usuario, password_hash, rol) VALUES (%s, %s, %s, %s)",
        (nombre, usuario, hash_password(password), rol), commit=True
    )
    log_action("agregar", f"Usuario creado: {usuario} ({rol})")
    return jsonify({"message": f"Usuario {usuario} creado correctamente"}), 201

@app.route("/api/usuarios/<int:user_id>", methods=["PUT"])
@require_auth(roles=["admin"])
def actualizar_usuario(user_id):
    data = request.get_json()
    user = query("SELECT * FROM usuarios WHERE id=%s", (user_id,), fetchone=True)
    if not user:
        return jsonify({"error": "Usuario no encontrado"}), 404
    if user_id == g.current_user["sub"]:
        return jsonify({"error": "No puedes modificar tu propia cuenta"}), 403

    activo = data.get("activo", user["activo"])
    rol    = data.get("rol",    user["rol"])
    query("UPDATE usuarios SET activo=%s, rol=%s WHERE id=%s", (activo, rol, user_id), commit=True)
    accion = "activado" if activo else "desactivado"
    log_action("editar", f"Usuario {accion}: {user['usuario']}")
    return jsonify({"message": "Usuario actualizado"})

@app.route("/api/usuarios/<int:user_id>", methods=["DELETE"])
@require_auth(roles=["admin"])
def eliminar_usuario(user_id):
    if user_id == g.current_user["sub"]:
        return jsonify({"error": "No puedes eliminar tu propia cuenta"}), 403
    user = query("SELECT * FROM usuarios WHERE id=%s", (user_id,), fetchone=True)
    if not user:
        return jsonify({"error": "Usuario no encontrado"}), 404
    query("DELETE FROM usuarios WHERE id=%s", (user_id,), commit=True)
    log_action("eliminar", f"Usuario eliminado: {user['usuario']}")
    return jsonify({"message": "Usuario eliminado"})

# ── ENDPOINT: Importar datos externos ────────────────────────────
@app.route("/api/importar", methods=["POST"])
@require_auth(roles=["admin", "operador"])
def importar_productos():
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "Se esperaba un arreglo JSON"}), 400

    importados = 0
    errores    = []
    for item in data:
        try:
            nombre   = item.get("nombre", "").strip()
            codigo   = item.get("codigo", "").strip().upper()
            tipo     = item.get("tipo", "Otro")
            cantidad = int(item.get("cantidad", 0))
            precio   = float(item.get("precio", 0))
            minimo   = int(item.get("minimo", 5))
            anio_raw = item.get("anio")
            anio     = int(anio_raw) if anio_raw else None
            mes      = item.get("mes", "") or None
            dia_raw  = item.get("dia")
            dia      = int(dia_raw) if dia_raw else None
            if not nombre or not codigo: continue
            existente = query("SELECT id FROM productos WHERE codigo=%s", (codigo,), fetchone=True)
            if existente: continue
            query(
                "INSERT INTO productos (nombre, codigo, tipo, cantidad, precio, stock_minimo, anio, mes, dia) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (nombre, codigo, tipo, cantidad, precio, minimo, anio, mes, dia), commit=True
            )
            importados += 1
        except Exception as e:
            errores.append(str(e))

    log_action("importar", f"Importados {importados} productos desde API externa")
    return jsonify({"importados": importados, "errores": errores}), 201

# ── Error handlers ────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e): return jsonify({"error": "Ruta no encontrada"}), 404

@app.errorhandler(500)
def server_error(e): return jsonify({"error": "Error interno del servidor"}), 500

# ── Main ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "true").lower() == "true"
    print(f"\n🚀  Backend activo en http://localhost:{port}")
    print(f"📦  BD: {DB_CONFIG['dbname']} @ {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print(f"🔧  Debug: {debug}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)