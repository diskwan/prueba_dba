# Demostración Técnica DBA: Point-In-Time Recovery (PITR) con pgBackRest en PostgreSQL 17

Solución técnica para recuperación ante desastres y mitigación de errores lógicos (`UPDATE` sin `WHERE`) en la plataforma Conecta Rural.

---

## 1. Requisitos Previos
- Docker y Docker Compose (V2) instalados.
- Python 3.

---

## 2. Cómo Levantar y Repetir la Demostración
Todo el flujo está completamente automatizado en un único script:

```bash
python3 demo.py
```

Para reiniciar y limpiar todos los volúmenes del entorno:
```bash
docker compose down -v
```

---

## 3. Supuestos y Tradeoffs

### Supuestos:
1. **Disponibilidad del motor tras el incidente:** Dado que el fallo fue un error lógico (`UPDATE` no calificado) y no una falla física de hardware, el motor PostgreSQL continúa operativo. Esto permite la extracción preventiva de las transacciones generadas con posterioridad al incidente.
2. **Entorno local autosuficiente:** Según las pautas de evaluación, no se utilizan servicios en la nube (S3, GCS, RDS); el repositorio de pgBackRest reside en un volumen persistente de Docker.

### Tradeoffs:
1. **Empaquetado en un solo Dockerfile:** Para asegurar máxima portabilidad, cero dependencias de imágenes externas no oficiales y una ejecución sin fricción en una sola máquina, se instaló pgBackRest junto con PostgreSQL 17 oficial. En producción, el repositorio debe alojarse en un host o bucket de objetos aislado.
2. **Rescate vía CSV:** Se utilizó extracción preventiva vía `COPY` al ser una demostración acotada a la tabla `postulaciones`. En un entorno multivariable con alta concurrencia y múltiples llaves foráneas, se requeriría una estrategia basada en replicación lógica o captura continua de cambios (CDC).

---

## 4. Preguntas Clave del Panel

### 1. Si se pierde el disco de la base, ¿cuántos datos se pierden (RPO)? ¿De qué depende?
- **RPO:** Tiende a cero (del orden de segundos).
- **Dependencia:** Depende de la frecuencia con la que los registros WAL se transfieren al repositorio externo (`archive_command`) y del parámetro `archive_timeout` (fijado en 300s en esta maqueta), que fuerza el cierre y envío de segmentos aun con baja carga transaccional.

### 2. ¿Cuánto tarda en volver a estar disponible (RTO)? ¿Cómo crece con el tamaño de la base?
- **RTO:** Tiempo de restauración del último backup base físico + tiempo de reproducción secuencial de los registros WAL generados hasta el punto de corte.
- **Crecimiento:** Con el volumen de la base crece principalmente la fase de restauración física. pgBackRest mitiga esto mediante copias multihilo (`process-max`) y transferencias delta (`--delta`). En bases de alto volumen, se combinan backups completos con respaldos diferenciales frecuentes.

### 3. ¿Qué pasó con lo escrito después del incidente y cómo lo trató?
- Por diseño, el PITR rebobina la base descartando todo cambio posterior al timestamp indicado.
- **Tratamiento:** Se realizó una extracción preventiva (`COPY`) de las postulaciones creadas tras el corte (`creado_en > TARGET_TIME`). Posterior a la recuperación de la base, se reinsertaron a través de una tabla temporal y se sincronizó el correlativo de la secuencia `SERIAL` (`setval`) para garantizar consistencia sin colisiones de identificadores.

### 4. ¿Dónde deben vivir los respaldos? ¿Qué pasa si están en el mismo disco o servidor que la base?
- Deben vivir en una infraestructura independiente y separada físicamente (ej. almacenamiento de objetos S3 o storage dedicado NFS).
- Si residen en el mismo disco o host físico, cualquier falla catastrófica de hardware o corrupción de volumen destruirá simultáneamente los datos operativos y los respaldos.

### 5. ¿Cómo sabría, un martes cualquiera, que los respaldos de verdad sirven?
- Un respaldo no existe hasta que se comprueba su restauración.
- Se implementan pipelines automáticos periódicos en un ambiente de staging que toman el último backup, ejecutan la restauración completa, validan integridad física y lógica (`pg_amcheck` y consultas de negocio) y emiten alertas si el proceso falla o excede el umbral de RTO.
