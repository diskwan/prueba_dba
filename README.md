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

## 4. Preguntas Clave del Panel y Jefatura

### 1. Si se pierde el disco de la base, ¿cuántos datos se pierden (RPO)? ¿De qué depende?

* **Datos que se perderían (RPO estimado):** Tiende a cero (del orden de segundos a pocos minutos en el peor de los casos).

* **De qué depende:**

  1. **Frecuencia de envío:** Depende de qué tan rápido viajan los registros de transacciones (WAL) al almacenamiento de respaldo y del parámetro `archive_timeout` (fijado en 300 segundos en esta maqueta), el cual fuerza el cierre y envío de registros aun con baja actividad en el sistema.

  2. **Carga del sistema y saturación de disco:** Si durante el incidente entra un volumen alto de transacciones o el disco presenta alta contención de I/O, el despacho de los registros WAL puede retrasarse, ampliando temporalmente la ventana de datos en riesgo.

  3. **Control operativo:** El sistema debe contar con alarmas automáticas que avisen al equipo de inmediato si los respaldos presentan desfase (*lag*) respecto al tráfico real, permitiendo intervenir antes de una falla física del equipo.

### 2. ¿Cuánto tarda en volver a estar disponible (RTO)? ¿Cómo crece con el tamaño de la base?

* **Tiempo estimado de recuperación (RTO):** Se compone de dos etapas sucesivas: el tiempo requerido para restaurar los archivos del respaldo base más el tiempo que tarda el motor de base de datos en leer y reprocesar secuencialmente los registros de transacciones acumulados hasta el punto de corte.

* **Cómo crece y qué lo condiciona:**

  1. **Copia física vs. reprocesamiento interno:** pgBackRest agiliza la descarga de archivos mediante múltiples hilos concurrentes (`process-max`) y transferencias delta (`--delta`). No obstante, la lectura y aplicación de transacciones (*redo/replay*) se procesa en un único hilo dentro del motor; ante una alta acumulación de operaciones, esta etapa se convierte en el cuello de botella.

  2. **Factores de hardware y sistema operativo:** El tiempo final está sujeto al rendimiento del almacenamiento (IOPS y velocidad de lectura/escritura), al sistema de archivos configurado y a los parámetros de memoria asignados al motor para transacciones y checkpoints.

  3. **Estrategia para bases de gran tamaño:** Para evitar que el RTO escale desmedidamente conforme crece el volumen de datos, la estrategia exige combinar respaldos completos semanales con respaldos diferenciales o incrementales frecuentes, reduciendo la cantidad de registros que el motor debe procesar uno a uno.

### 3. ¿Qué pasó con lo escrito después del incidente y cómo lo trató?

* **Comportamiento por diseño de PITR:**
  Al aplicar esta técnica, la base de datos se retrocede en el tiempo hasta el segundo exacto previo a la instrucción errónea. Por diseño de PostgreSQL, todo lo ocurrido después de esa marca temporal queda fuera de la base restaurada al promoverse una nueva línea de tiempo (*timeline*).

* **Tratamiento en la demostración:**
  Como el motor seguía encendido y accesible para consultas de lectura, se ejecutó un rescate preventivo: se extrajeron a un archivo CSV las solicitudes legítimas ingresadas tras el corte (`creado_en > TARGET_TIME`). Posterior a la recuperación con pgBackRest, se reinyectaron esas filas mediante una tabla temporal y se sincronizó el correlativo de la secuencia autoincremental (`setval`), impidiendo colisiones en los trámites siguientes.

* **Criterio para un entorno de producción real:**
  En una plataforma con alta concurrencia ciudadana, rescatar datos manualmente con archivos planos sobre la misma base no es la mejor opción por riesgo de errores humanos o inconsistencias relacionales. La práctica recomendada en producción es **no sobrescribir la base afectada**: se despliega una instancia paralela temporal (un clon), se ejecuta el PITR allí, se valida la información y se concilian selectivamente las transacciones válidas antes del traspaso definitivo del tráfico.

### 4. ¿Dónde deben vivir los respaldos? ¿Qué pasa si están en el mismo disco o servidor que la base?

* **El riesgo principal:**
  Si las copias de seguridad residen en el mismo disco o servidor físico de la base de datos, se genera un punto único de falla. Ante un daño irreparable de disco, corrupción del sistema de archivos o siniestro del servidor, se destruyen simultáneamente los datos de producción y los respaldos.

* **Dónde deben vivir (Estrategia por capas):**

  1. **Copia local inmediata:** Conviene mantener una copia reciente en un almacenamiento secundario dentro del mismo entorno para resolver fallos lógicos o humanos con máxima velocidad, evitando demoras de transferencia por red.

  2. **Copia externa y aislada:** Al mismo tiempo, los respaldos y registros WAL deben transferirse continuamente a una infraestructura independiente (almacenamiento en red aislado o almacenamiento de objetos en la nube), garantizando la supervivencia de los datos ante catástrofes mayores o pérdida total del equipamiento.

### 5. ¿Cómo sabría, un martes cualquiera, que los respaldos de verdad sirven?

* **Principio fundamental:** Un respaldo no se considera válido hasta que se demuestra que puede restaurarse e iniciar operaciones exitosamente.

* **Verificación técnica diaria y automatizada:**
  Se implementan tareas programadas en un entorno de pruebas aislado que descargan el último respaldo generado, ejecutan una restauración completa, levantan el motor y corren comprobaciones de integridad física y lógica (`pg_amcheck` y consultas de negocio). Si la restauración falla o excede el tiempo estimado, se emiten alertas inmediatas al equipo de operaciones.

* **Simulacros periódicos de continuidad de servicio:**
  De forma periódica y planificada, se ejecutan ejercicios integrales de recuperación ante desastres (*DR drills*). Estas pruebas simulan escenarios de indisponibilidad total para medir y cronometrar en condiciones reales cuánto tarda el equipo y la plataforma en restablecer la atención a los usuarios, certificando que los tiempos comprometidos (RTO y RPO) se cumplan operativamente.
