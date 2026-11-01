# Registro de Interacción con Asistente de IA

## Prompt 1: Arquitectura Base
**Usuario:**
Hola. Necesito resolver un escenario de alta disponibilidad y recuperación ante desastres para PostgreSQL 17 usando Docker Compose. El objetivo es mitigar un incidente donde se corrió un UPDATE sin WHERE en una tabla crítica ('postulaciones') y el único respaldo previo era un pg_dump semanal. Requiero configurar archivado continuo de WAL y Point-In-Time Recovery (PITR) con pgBackRest, 100% en local sin servicios en la nube. ¿Cuál es la arquitectura mínima en Docker y las directivas necesarias en postgresql.conf y pgbackrest.conf para lograr esto?

**Respuesta Técnica:**
Se propuso la creación de una imagen basada en PostgreSQL 17 oficial con pgBackRest instalado, configurando las directivas `wal_level = replica`, `archive_mode = on`, `archive_command = 'pgbackrest --stanza=app_stanza archive-push %p'` y `archive_timeout = 300` para asegurar el flujo continuo de WAL hacia un volumen persistente.

---

## Prompt 2: Creación de Datos y Primer Respaldo
**Usuario:**
Entendido. Ahora preparemos el entorno de prueba con datos sintéticos para la tabla 'postulaciones' (id, rut, nombre, estado, creado_en). Dame los comandos para inicializar la stanza de pgBackRest, validar con 'check' que el repositorio esté operativo y tomar el primer respaldo completo. Además, agrega un registro legítimo posterior al respaldo para verificar que el WAL capture cambios intermedios.

**Respuesta Técnica:**
Se entregó el esquema DDL con campo `creado_en TIMESTAMPTZ DEFAULT clock_timestamp()`, comandos para `stanza-create`, verificación de transporte con `check`, toma de backup físico (`--type=full`) y comprobación de archivado continuo con `pg_switch_wal()`.

---

## Prompt 3: Estrategia de Negocio ante Datos Posteriores
**Usuario:**
Tengo una duda sobre el requerimiento: supongamos que marco la hora exacta de corte antes del UPDATE sin WHERE. Si después del error entran nuevas postulaciones legítimas y luego ejecuto el restore por tiempo con pgBackRest, por definición de PITR esos datos posteriores se van a perder. La jefatura no quiere perder esas postulaciones nuevas. ¿Qué estrategia técnica me recomiendas para rescatar y reincorporar esas filas sin colisionar identificadores antes de reiniciar el motor?

**Respuesta Técnica:**
Dado que el motor sigue accesible tras un fallo meramente lógico, se recomendó una extracción preventiva vía `COPY` filtrando por `creado_en > TARGET_TIME` hacia un archivo plano antes de bajar el servicio. Luego del restore PITR, los registros se reincorporan mediante una tabla temporal y se actualiza el valor de la secuencia autoincremental con `setval(pg_get_serial_sequence('postulaciones', 'id'), ...)` para evitar colisiones.

---

## Prompt 4: Simulación del Incidente y Secuencia de Recuperación
**Usuario:**
Apliquemos esa estrategia de rescate preventivo. Dame la secuencia exacta de comandos para: 1) guardar el timestamp de corte, 2) correr el UPDATE sin WHERE masivo, 3) insertar las 2 postulaciones nuevas, 4) extraer preventivamente esas filas con COPY, 5) forzar pg_switch_wal, detener el cluster y ejecutar el restore delta con pgBackRest hasta el target de corte, y 6) levantar y reinyectar el delta rescatado.

**Respuesta Técnica:**
Se suministró la secuencia en shell con el uso de `--target-action=promote` y `--delta` en pgBackRest, resolviendo la extracción a CSV y la reincorporación de datos por canal de entrada estándar.

---

## Prompt 5: Automatización Completa en Python
**Usuario:**
Para cumplir con la pauta de 'todo se levanta con un solo comando' y tener una demo limpia de 10 minutos para la presentación, unifiquemos todo este flujo en un script en Python (demo.py). El script debe capturar el timestamp dinámicamente, mostrar los estados de la base en cada etapa (antes, corrupta, restaurada, final consistente) y no depender de servicios externos. Proporcióname el código completo.

**Respuesta Técnica:**
Se construyó `demo.py`, automatizando el ciclo de vida completo con subprocesos de Python, inyección limpia de SQL vía entrada estándar (`stdin`), validaciones de disponibilidad con `pg_isready` y salida por consola de cada estado de la tabla.
