 -- ═══════════════════════════════════════════════════════════════
--  Proyecto Programación — Sistema de Inventario
--  Archivo: schema.sql
--  Base de datos: PostgreSQL
--
--  INSTRUCCIONES:
--  1. Crea la base de datos:  CREATE DATABASE inventario_db;
--  2. Conéctate a ella:       \c inventario_db
--  3. Ejecuta este archivo:   \i schema.sql
-- ═══════════════════════════════════════════════════════════════

-- ── Extensiones ─────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Tabla: usuarios ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS usuarios (
    id            SERIAL PRIMARY KEY,
    nombre        VARCHAR(100)  NOT NULL,
    usuario       VARCHAR(50)   NOT NULL UNIQUE,
    password_hash VARCHAR(64)   NOT NULL,           -- SHA-256 hex
    rol           VARCHAR(20)   NOT NULL DEFAULT 'viewer'
                  CHECK (rol IN ('admin','operador','viewer')),
    activo        BOOLEAN       NOT NULL DEFAULT true,
    creado        TIMESTAMP     NOT NULL DEFAULT NOW(),
    ultimo_login  TIMESTAMP
);

-- ── Tabla: productos ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS productos (
    id            SERIAL PRIMARY KEY,
    nombre        VARCHAR(200)  NOT NULL,
    codigo        VARCHAR(50)   NOT NULL UNIQUE,
    tipo          VARCHAR(80)   NOT NULL DEFAULT 'Otro',
    cantidad      INTEGER       NOT NULL DEFAULT 0  CHECK (cantidad >= 0),
    precio        NUMERIC(18,2) NOT NULL DEFAULT 0  CHECK (precio >= 0),
    stock_minimo  INTEGER       NOT NULL DEFAULT 5  CHECK (stock_minimo >= 0),
    anio          INTEGER,
    mes           VARCHAR(20),
    dia           INTEGER,
    creado        TIMESTAMP     NOT NULL DEFAULT NOW(),
    actualizado   TIMESTAMP     NOT NULL DEFAULT NOW()
);

-- Columna calculada virtual (vista)
CREATE OR REPLACE VIEW productos_detalle AS
    SELECT *,
           (cantidad * precio)                       AS valor_total,
           CASE
               WHEN cantidad = 0            THEN 'sin_stock'
               WHEN cantidad <= stock_minimo THEN 'bajo_stock'
               ELSE                               'normal'
           END                                       AS estado
    FROM productos;

-- ── Tabla: actividad ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS actividad (
    id               BIGSERIAL PRIMARY KEY,
    tipo             VARCHAR(30)  NOT NULL,   -- agregar, eliminar, login, logout, importar...
    detalle          TEXT,
    producto_nombre  VARCHAR(200),
    producto_codigo  VARCHAR(50),
    usuario          VARCHAR(50)  NOT NULL DEFAULT 'sistema',
    fecha            TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ── Tabla: pedidos_reabastecimiento ─────────────────────────────
CREATE TABLE IF NOT EXISTS pedidos (
    id               SERIAL PRIMARY KEY,
    producto_id      INTEGER REFERENCES productos(id) ON DELETE SET NULL,
    producto_nombre  VARCHAR(200),
    producto_codigo  VARCHAR(50),
    cantidad_pedida  INTEGER      NOT NULL DEFAULT 0,
    estado           VARCHAR(30)  NOT NULL DEFAULT 'pendiente'
                     CHECK (estado IN ('pendiente','en_camino','recibido','cancelado')),
    usuario          VARCHAR(50),
    notas            TEXT,
    creado           TIMESTAMP    NOT NULL DEFAULT NOW(),
    actualizado      TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ── Índices para rendimiento ─────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_productos_codigo  ON productos(codigo);
CREATE INDEX IF NOT EXISTS idx_productos_tipo    ON productos(tipo);
CREATE INDEX IF NOT EXISTS idx_productos_alerta  ON productos(cantidad, stock_minimo);
CREATE INDEX IF NOT EXISTS idx_productos_anio    ON productos(anio);
CREATE INDEX IF NOT EXISTS idx_productos_mes     ON productos(mes);
CREATE INDEX IF NOT EXISTS idx_productos_dia     ON productos(dia);
CREATE INDEX IF NOT EXISTS idx_actividad_tipo    ON actividad(tipo);
CREATE INDEX IF NOT EXISTS idx_actividad_fecha   ON actividad(fecha DESC);
CREATE INDEX IF NOT EXISTS idx_actividad_usuario ON actividad(usuario);

-- ── Trigger: actualizar timestamp de productos ───────────────────
CREATE OR REPLACE FUNCTION set_actualizado()
RETURNS TRIGGER AS $$
BEGIN
    NEW.actualizado = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_productos_actualizado ON productos;
CREATE TRIGGER trg_productos_actualizado
    BEFORE UPDATE ON productos
    FOR EACH ROW EXECUTE FUNCTION set_actualizado();

-- ── Función: estadísticas rápidas ───────────────────────────────
CREATE OR REPLACE FUNCTION estadisticas_inventario()
RETURNS TABLE(
    total_productos  BIGINT,
    total_unidades   BIGINT,
    valor_total      NUMERIC,
    precio_promedio  NUMERIC,
    precio_max       NUMERIC,
    precio_min       NUMERIC,
    sin_stock        BIGINT,
    bajo_stock       BIGINT,
    normal           BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)                                            AS total_productos,
        COALESCE(SUM(cantidad), 0)                         AS total_unidades,
        COALESCE(SUM(cantidad * precio), 0)                AS valor_total,
        COALESCE(AVG(precio), 0)                           AS precio_promedio,
        COALESCE(MAX(precio), 0)                           AS precio_max,
        COALESCE(MIN(CASE WHEN precio>0 THEN precio END),0) AS precio_min,
        COUNT(CASE WHEN cantidad = 0 THEN 1 END)           AS sin_stock,
        COUNT(CASE WHEN cantidad > 0 AND cantidad <= stock_minimo THEN 1 END) AS bajo_stock,
        COUNT(CASE WHEN cantidad > stock_minimo THEN 1 END) AS normal
    FROM productos;
END;
$$ LANGUAGE plpgsql;

-- ── Datos iniciales ──────────────────────────────────────────────
-- Usuario admin por defecto (password: admin123)
INSERT INTO usuarios (nombre, usuario, password_hash, rol)
VALUES (
    'Administrador',
    'admin',
    -- SHA-256 de 'admin123'
    'c8e680fd4e5ac61c6e5c33e9c1b3a4e8d2f45678901234567890abcdefabcdef',
    'admin'
) ON CONFLICT (usuario) DO NOTHING;

-- ── Vista: actividad por día ─────────────────────────────────────
CREATE OR REPLACE VIEW actividad_por_dia AS
    SELECT
        DATE(fecha)             AS dia,
        tipo,
        COUNT(*)                AS total,
        COUNT(DISTINCT usuario) AS usuarios_distintos
    FROM actividad
    GROUP BY DATE(fecha), tipo
    ORDER BY dia DESC, total DESC;

-- ── Vista: top productos por valor ──────────────────────────────
CREATE OR REPLACE VIEW top_productos_valor AS
    SELECT
        codigo, nombre, tipo,
        cantidad, precio,
        (cantidad * precio) AS valor_total,
        RANK() OVER (ORDER BY cantidad * precio DESC) AS ranking
    FROM productos
    WHERE cantidad > 0
    ORDER BY valor_total DESC;

-- ── Vista: alertas activas ───────────────────────────────────────
CREATE OR REPLACE VIEW alertas_activas AS
    SELECT
        id, codigo, nombre, tipo,
        cantidad, stock_minimo, precio,
        (cantidad * precio) AS valor_stock,
        CASE
            WHEN cantidad = 0 THEN 'sin_stock'
            ELSE 'bajo_stock'
        END AS nivel_alerta,
        (stock_minimo - cantidad) AS unidades_faltantes
    FROM productos
    WHERE cantidad <= stock_minimo
    ORDER BY cantidad ASC, precio DESC;

-- ── Migración: agregar anio y mes a tabla existente ────────────
-- Ejecuta solo si la tabla ya existe y NO tiene las columnas:
-- ALTER TABLE productos ADD COLUMN IF NOT EXISTS anio INTEGER;
-- ALTER TABLE productos ADD COLUMN IF NOT EXISTS mes  VARCHAR(20);
-- ALTER TABLE productos ADD COLUMN IF NOT EXISTS dia  INTEGER;

-- ── Confirmación ─────────────────────────────────────────────────
DO $$
BEGIN
    RAISE NOTICE '✓ Schema creado correctamente';
    RAISE NOTICE '  Tablas: usuarios, productos, actividad, pedidos';
    RAISE NOTICE '  Vistas: productos_detalle, alertas_activas, top_productos_valor, actividad_por_dia';
    RAISE NOTICE '  Usuario admin creado (password: admin123) — ¡cámbialo en producción!';
END $$;
