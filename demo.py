#!/usr/bin/env python3
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CSV_FILE = BASE_DIR / "delta_postulaciones.csv"

def log_step(title: str):
    print("\n" + "=" * 70)
    print(f" >>> {title}")
    print("=" * 70)

def run_cmd(cmd: list[str], check: bool = True, capture: bool = False) -> str:
    res = subprocess.run(cmd, text=True, capture_output=capture)
    if check and res.returncode != 0:
        if capture:
            print(f" [ERROR STDOUT]: {res.stdout}")
            print(f" [ERROR STDERR]: {res.stderr}")
        raise RuntimeError(f"Fallo el comando: {' '.join(cmd)}")
    return res.stdout if capture else ""

def psql(query: str, capture: bool = False) -> str:
    # -A (sin alineacion), -t (solo tuplas sin cabeceras ni pie), -q (modo silencioso)
    cmd = [
        "docker", "compose", "exec", "-T", "-u", "postgres", "db",
        "psql", "-d", "appdb", "-A", "-t", "-q"
    ]
    proc = subprocess.run(
        cmd,
        input=query,
        text=True,
        capture_output=True
    )
    if proc.returncode != 0:
        print(f" [ERROR STDERR]: {proc.stderr}")
        raise RuntimeError(f"Fallo la consulta SQL:\n{query}")
    return proc.stdout if capture else ""

def psql_table(query: str) -> str:
    # Consulta con formato visual de tabla para mostrar en pantalla
    cmd = [
        "docker", "compose", "exec", "-T", "-u", "postgres", "db",
        "psql", "-d", "appdb"
    ]
    proc = subprocess.run(
        cmd,
        input=query,
        text=True,
        capture_output=True
    )
    if proc.returncode != 0:
        print(f" [ERROR STDERR]: {proc.stderr}")
        raise RuntimeError(f"Fallo la consulta SQL:\n{query}")
    return proc.stdout

def print_table_state(label: str):
    print(f"\n[ESTADO: {label}]")
    out = psql_table("SELECT id, rut, nombre, estado, to_char(creado_en, 'HH24:MI:SS.US') as hora FROM postulaciones ORDER BY id;")
    print(out.strip())

def wait_for_postgres(timeout: int = 30):
    start = time.time()
    while time.time() - start < timeout:
        res = subprocess.run(
            ["docker", "compose", "exec", "-T", "-u", "postgres", "db", "pg_isready", "-d", "appdb"],
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            return
        time.sleep(1)
    raise TimeoutError("PostgreSQL no respondio a tiempo.")

def main():
    log_step("Paso 0: Preparacion y arranque de contenedores")
    print("Limpiando entorno previo...")
    run_cmd(["docker", "compose", "down", "-v"], check=False)
    print("Construyendo imagen y levantando servicio...")
    run_cmd(["docker", "compose", "up", "-d", "--build"])
    wait_for_postgres()

    log_step("Paso 1: Inicializacion de base, datos iniciales y backup completo")
    psql("""
    DROP TABLE IF EXISTS postulaciones;
    CREATE TABLE postulaciones (
        id SERIAL PRIMARY KEY,
        rut VARCHAR(12) NOT NULL,
        nombre VARCHAR(100) NOT NULL,
        estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
        creado_en TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    INSERT INTO postulaciones (rut, nombre, estado) VALUES
    ('11.111.111-1', 'Juan Perez', 'PENDIENTE'),
    ('22.222.222-2', 'Maria Gonzalez', 'APROBADO'),
    ('33.333.333-3', 'Carlos Tapia', 'PENDIENTE'),
    ('44.444.444-4', 'Andrea Soto', 'RECHAZADO'),
    ('55.555.555-5', 'Roberto Diaz', 'PENDIENTE');
    """)

    print("Inicializando stanza en pgBackRest...")
    run_cmd(["docker", "compose", "exec", "-T", "-u", "postgres", "db", "pgbackrest", "--stanza=app_stanza", "stanza-create"])
    run_cmd(["docker", "compose", "exec", "-T", "-u", "postgres", "db", "pgbackrest", "--stanza=app_stanza", "check"])

    print("Ejecutando primer backup completo fisico...")
    run_cmd(["docker", "compose", "exec", "-T", "-u", "postgres", "db", "pgbackrest", "--stanza=app_stanza", "--type=full", "backup"])

    print("Insertando registro posterior al backup full...")
    psql("INSERT INTO postulaciones (rut, nombre, estado) VALUES ('66.666.666-6', 'Valeria Munoz', 'PENDIENTE');")
    psql("SELECT pg_switch_wal();")
    print_table_state("1. ANTES DEL INCIDENTE (DATOS INTEGROS)")

    log_step("Paso 2: Captura del timestamp de corte y simulacion del UPDATE sin WHERE")
    time.sleep(1)
    target_time = psql("SELECT clock_timestamp();", capture=True).strip()
    print(f"Timestamp de corte para PITR: {target_time}")
    time.sleep(1)

    print("Ejecutando: UPDATE postulaciones SET estado = 'RECHAZADO_ERROR'; (Sin WHERE)")
    psql("UPDATE postulaciones SET estado = 'RECHAZADO_ERROR';")
    print_table_state("2. DESPUES DEL ERROR (BASE CORRUPTA)")

    log_step("Paso 3: Llegada de postulaciones legitimas post-incidente")
    psql("""
    INSERT INTO postulaciones (rut, nombre, estado) VALUES
    ('77.777.777-7', 'Daniela Lagos', 'PENDIENTE'),
    ('88.888.888-8', 'Gonzalo Morales', 'APROBADO');
    """)
    print_table_state("3. CON FILAS NUEVAS QUE NO DEBEN PERDERSE")

    log_step("Paso 4: Rescate preventivo de filas posteriores a CSV")
    copy_query = f"""
    COPY (
        SELECT id, rut, nombre, estado, creado_en
        FROM postulaciones
        WHERE creado_en > '{target_time}'
        ORDER BY id ASC
    ) TO STDOUT WITH CSV HEADER;
    """
    csv_data = psql(copy_query, capture=True)
    CSV_FILE.write_text(csv_data)
    print(f"Filas respaldadas localmente en '{CSV_FILE.name}':\n{csv_data.strip()}")

    log_step("Paso 5: Forzar archivado de WAL, detencion y PITR con pgBackRest")
    psql("SELECT pg_switch_wal();")
    print("Deteniendo contenedor de base de datos...")
    run_cmd(["docker", "compose", "stop", "db"])

    print(f"Ejecutando restore delta con pgBackRest hasta '{target_time}'...")
    restore_cmd = [
        "docker", "compose", "run", "--rm", "-T",
        "--user", "postgres",
        "db",
        "pgbackrest", "--stanza=app_stanza",
        "--type=time", f"--target={target_time}",
        "--target-action=promote",
        "--delta",
        "restore"
    ]
    run_cmd(restore_cmd)

    print("Iniciando servicio de base de datos recuperado...")
    run_cmd(["docker", "compose", "up", "-d", "db"])
    wait_for_postgres()
    print_table_state("4. POST RESTORE PITR (ORIGINALES RECUPERADOS, FALTAN POSTERIORES)")

    log_step("Paso 6: Reinyectar el delta rescatado y resincronizar secuencia SERIAL")
    reinject_sql = """
    CREATE TEMP TABLE tmp_delta (
        id INT,
        rut VARCHAR(12),
        nombre VARCHAR(100),
        estado VARCHAR(20),
        creado_en TIMESTAMPTZ
    );
    \\copy tmp_delta FROM PSTDIN WITH CSV HEADER;
    INSERT INTO postulaciones (id, rut, nombre, estado, creado_en)
    SELECT id, rut, nombre, estado, creado_en FROM tmp_delta;
    SELECT setval(pg_get_serial_sequence('postulaciones', 'id'), COALESCE(MAX(id), 1))
    FROM postulaciones;
    DROP TABLE tmp_delta;
    """
    reinject_proc = subprocess.Popen(
        ["docker", "compose", "exec", "-T", "-u", "postgres", "db", "psql", "-d", "appdb"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    payload = reinject_sql + "\n" + CSV_FILE.read_text()
    stdout_r, stderr_r = reinject_proc.communicate(input=payload)
    if reinject_proc.returncode != 0:
        print(stderr_r)
        raise RuntimeError("Error al reinyectar delta.")

    print_table_state("5. ESTADO FINAL CONSISTENTE (TODO RECUPERADO SIN COLISIONES)")
    print("\n\033[1;32mDemostracion completada exitosamente.\033[0m")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[ERROR CRITICO]: {e}", file=sys.stderr)
        sys.exit(1)
