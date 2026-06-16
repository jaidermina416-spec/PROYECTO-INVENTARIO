"""
importar_inventario.py
──────────────────────
Lee el CSV de inventario y lo carga directo en PostgreSQL.

USO:
  1. Pon este script en la misma carpeta que el CSV
  2. Ajusta las variables de conexión abajo (o crea un .env)
  3. Ejecuta:  python importar_inventario.py
"""

import os, sys
import pandas as pd
import psycopg2
import psycopg2.extras

# ── Configuración de conexión ─────────────────────────────────────
# Puedes cambiar estos valores aquí directamente, o usar variables de entorno
DB_HOST     = os.environ.get("DB_HOST",     "localhost")
DB_PORT     = int(os.environ.get("DB_PORT", 5432))
DB_NAME     = os.environ.get("DB_NAME",     "inventario_db")
DB_USER     = os.environ.get("DB_USER",     "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "0912")

# ── Ruta del CSV ──────────────────────────────────────────────────
# Si el CSV está en la misma carpeta que este script:
CSV_PATH = os.path.join(os.path.dirname(__file__), "inventario.csv")
# O pon la ruta completa, por ejemplo:
# CSV_PATH = r"C:\Users\TuNombre\Desktop\inventario.csv"

# ── Crear tabla si no existe ──────────────────────────────────────
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.productos (
    id           SERIAL PRIMARY KEY,
    nombre       VARCHAR(255) NOT NULL,
    codigo       VARCHAR(100) UNIQUE NOT NULL,
    tipo         VARCHAR(100) DEFAULT 'Otro',
    cantidad     INTEGER      DEFAULT 0,
    precio       NUMERIC(12,2) DEFAULT 0,
    stock_minimo INTEGER      DEFAULT 5,
    anio         INTEGER,
    mes          VARCHAR(20),
    dia          INTEGER,
    actualizado  TIMESTAMP    DEFAULT NOW()
);
-- Si la tabla ya existe sin estas columnas, ejecuta:
-- ALTER TABLE public.productos ADD COLUMN IF NOT EXISTS anio INTEGER;
-- ALTER TABLE public.productos ADD COLUMN IF NOT EXISTS mes  VARCHAR(20);
-- ALTER TABLE public.productos ADD COLUMN IF NOT EXISTS dia  INTEGER;

CREATE TABLE IF NOT EXISTS public.actividad (
    id               SERIAL PRIMARY KEY,
    tipo             VARCHAR(50),
    detalle          TEXT,
    producto_nombre  VARCHAR(255),
    producto_codigo  VARCHAR(100),
    usuario          VARCHAR(100),
    fecha            TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.usuarios (
    id            SERIAL PRIMARY KEY,
    nombre        VARCHAR(255) NOT NULL,
    usuario       VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(64)  NOT NULL,
    rol           VARCHAR(20)  DEFAULT 'viewer',
    activo        BOOLEAN      DEFAULT TRUE,
    creado        TIMESTAMP    DEFAULT NOW()
);
"""

INSERT_SQL = """
INSERT INTO public.productos (codigo, nombre, tipo, cantidad, precio, stock_minimo, anio, mes, dia)
VALUES (%(codigo)s, %(nombre)s, %(tipo)s, %(cantidad)s, %(precio)s, %(stock_minimo)s, %(anio)s, %(mes)s, %(dia)s)
ON CONFLICT (codigo) DO UPDATE SET
    nombre       = EXCLUDED.nombre,
    tipo         = EXCLUDED.tipo,
    cantidad     = EXCLUDED.cantidad,
    precio       = EXCLUDED.precio,
    stock_minimo = EXCLUDED.stock_minimo,
    anio         = EXCLUDED.anio,
    mes          = EXCLUDED.mes,
    dia          = EXCLUDED.dia,
    actualizado  = NOW();
"""

def leer_csv(path):
    """Intenta leer el CSV con varias codificaciones comunes en Windows."""
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(path, encoding=encoding)
            print(f"   ✓ Codificación detectada: {encoding}")
            return df
        except UnicodeDecodeError:
            continue
    print("❌ No se pudo leer el CSV con ninguna codificación conocida (utf-8, latin-1, cp1252).")
    sys.exit(1)

def main():
    # 1. Leer CSV
    print(f"📂 Leyendo CSV: {CSV_PATH}")
    try:
        df = leer_csv(CSV_PATH)
    except FileNotFoundError:
        print(f"❌ No se encontró el archivo: {CSV_PATH}")
        print("   Asegúrate de que el CSV esté en la misma carpeta que este script,")
        print("   o edita la variable CSV_PATH con la ruta completa.")
        sys.exit(1)

    print(f"   ✓ {len(df)} filas cargadas — columnas: {df.columns.tolist()}")

    # 2. Mapear columnas CSV → tabla productos
    registros = []
    for _, row in df.iterrows():
        # Leer Año y Mes si existen en el CSV (columnas opcionales)
        anio_val = row.get("Año") if "Año" in df.columns else None
        mes_val  = row.get("Mes") if "Mes" in df.columns else None
        dia_val  = row.get("Dia") if "Dia" in df.columns else None
        registros.append({
            "codigo":       str(row["Codigo"]).strip().upper(),
            "nombre":       str(row["Nombre"]).strip(),
            "tipo":         str(row["Tipo"]).strip(),
            "cantidad":     int(row["Cantidad"]),
            "precio":       float(row["Precio"]),
            "stock_minimo": int(row["Minimo"]),   # "Minimo" del CSV → stock_minimo en BD
            "anio":         int(anio_val) if (anio_val is not None and str(anio_val).strip() != "" and str(anio_val) != "nan") else None,
            "mes":          str(mes_val).strip() if (mes_val is not None and str(mes_val) != "nan") else None,
            "dia":          int(dia_val) if (dia_val is not None and str(dia_val).strip() != "" and str(dia_val) != "nan") else None,
        })

    # 3. Conectar a PostgreSQL
    print(f"\n🔌 Conectando a PostgreSQL ({DB_HOST}:{DB_PORT}/{DB_NAME})...")
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD
        )
        conn.autocommit = False
        cur = conn.cursor()
        print("   ✓ Conexión exitosa")
    except psycopg2.OperationalError as e:
        print(f"❌ No se pudo conectar:\n   {e}")
        print("\n💡 Verifica:")
        print("   - DB_PASSWORD está correcto en este script")
        print("   - PostgreSQL está corriendo (puerto 5432)")
        print("   - La base de datos 'inventario_db' existe")
        sys.exit(1)

    # 4. Crear tablas y migrar columnas nuevas si la tabla ya existe
    print("\n🏗️  Creando tablas si no existen...")
    cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    print("   ✓ Tablas listas")

    # Migración automática: agregar anio y mes si no existen
    print("\n🔄  Verificando columnas anio / mes...")
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'productos'
          AND column_name IN ('anio', 'mes', 'dia');
    """)
    columnas_existentes = {row[0] for row in cur.fetchall()}

    if 'anio' not in columnas_existentes:
        print("   ➕ Agregando columna 'anio'...")
        cur.execute("ALTER TABLE public.productos ADD COLUMN anio INTEGER;")
        conn.commit()
        print("   ✓ Columna 'anio' agregada")
    else:
        print("   ✓ Columna 'anio' ya existe")

    if 'mes' not in columnas_existentes:
        print("   ➕ Agregando columna 'mes'...")
        cur.execute("ALTER TABLE public.productos ADD COLUMN mes VARCHAR(20);")
        conn.commit()
        print("   ✓ Columna 'mes' agregada")
    else:
        print("   ✓ Columna 'mes' ya existe")

    if 'dia' not in columnas_existentes:
        print("   ➕ Agregando columna 'dia'...")
        cur.execute("ALTER TABLE public.productos ADD COLUMN dia INTEGER;")
        conn.commit()
        print("   ✓ Columna 'dia' agregada")
    else:
        print("   ✓ Columna 'dia' ya existe")

    # 5. Insertar registros
    print(f"\n📥 Insertando {len(registros)} productos...")
    try:
        psycopg2.extras.execute_batch(cur, INSERT_SQL, registros, page_size=100)
        conn.commit()
        print(f"   ✓ {len(registros)} productos importados / actualizados")
    except Exception as e:
        conn.rollback()
        print(f"❌ Error durante la inserción:\n   {e}")
        cur.close(); conn.close()
        sys.exit(1)

    # 6. Verificar
    cur.execute("SELECT COUNT(*) FROM public.productos;")
    total = cur.fetchone()[0]
    print(f"\n📊 Total en la tabla productos: {total} registros")

    # 7. Usuario admin por defecto (si la tabla usuarios está vacía)
    cur.execute("SELECT COUNT(*) FROM public.usuarios;")
    if cur.fetchone()[0] == 0:
        import hashlib
        pw_hash = hashlib.sha256("admin123".encode()).hexdigest()
        cur.execute(
            "INSERT INTO public.usuarios (nombre, usuario, password_hash, rol) VALUES (%s,%s,%s,%s)",
            ("Administrador", "admin", pw_hash, "admin")
        )
        conn.commit()
        print("\n👤 Usuario por defecto creado:")
        print("   usuario:  admin")
        print("   password: admin123  ← ¡Cámbiala pronto!")

    cur.close()
    conn.close()
    print("\n✅ ¡Importación completada! Ya puedes usar tu app HTML con el backend Flask.")

if __name__ == "__main__":
    main()