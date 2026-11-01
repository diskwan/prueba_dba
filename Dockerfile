FROM postgres:17

RUN apt-get update && \
    apt-get install -y pgbackrest && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p -m 770 /var/log/pgbackrest /var/lib/pgbackrest /etc/pgbackrest && \
    chown -R postgres:postgres /var/log/pgbackrest /var/lib/pgbackrest /etc/pgbackrest
