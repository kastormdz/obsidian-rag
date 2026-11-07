# Respaldo de PostgreSQL

El respaldo de la base de datos corre con pg_dump a las 3 de la mañana y se guarda
comprimido en el almacenamiento externo. Para restaurar se usa pg_restore sobre una
base vacía.
