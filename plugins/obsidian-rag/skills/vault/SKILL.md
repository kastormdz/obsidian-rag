---
name: vault
description: Buscar, leer y escribir en el vault de Obsidian del usuario a través de los tools vault_*.
---

# El vault del usuario

Un vault de Obsidian indexado con obsidian-rag. Los tools son un envoltorio del binario
`obsn`: ellos hacen la búsqueda híbrida, leen y escriben los `.md`, y mantienen el índice.

## El orden que funciona

1. **`vault_search`** antes de responder cualquier cosa que pueda estar en sus notas. Es
   fuente primaria, no último recurso: el vault tiene infra, finanzas, config, seguridad y
   vida personal.
2. **`vault_read`** sobre el hit que interesa. El snippet de la búsqueda es un pasaje, no la
   nota: para citar o editar, leé la nota entera.
3. Recién ahí contestás o escribís.

## Buscar bien

- Empezá en `hybrid`. Entiende paráfrasis y devuelve el heading de cada pasaje.
- Si `hybrid` no trae nada, cambiá a `mode: exact` con una palabra que sepas que está en el
  texto (un nombre de servicio, un comando, una sigla). El substring no perdona.
- Si tampoco, **`vault_status`**: confirma a qué vault y a qué índice está apuntando y
  cuántas notas ve. Un índice de otro vault explica todos los vacíos.
- `folder` y `tag` sólo existen en `mode: exact`. En `hybrid` se filtran descartando hits.

## Escribir sin ensuciar el vault

- **Buscá antes de crear.** Si ya hay una nota del mismo tema, va como sección adentro con
  `mode: append`, no como nota paralela con nombre nuevo.
- Los paths son relativos a la raíz del vault y con la carpeta adentro:
  `01-Technical/docker.md`. Sin barra inicial.
- `mode: create` pisa el archivo entero. Para agregar sin perder nada: `append` o `prepend`.
  Para cambiar una frase: `replace` con el texto exacto en `old`.
- No inventes carpetas nuevas sin mirar `vault_list` con `mode: folders`: el vault ya tiene
  una estructura y las notas fuera de lugar no aparecen en los índices del usuario.

## Borrar y mover

- `vault_delete` manda a `.trash` por defecto: es recuperable, pero igual conviene decirlo
  al usuario cuando se borra algo que él no pidió borrar.
- `permanent: true` no se deshace. Pedí confirmación explícita antes.
- `vault_move` no pisa: si el destino existe, falla. Renombrá con intención.

## Después de escribir

El índice se actualiza solo, en segundo plano, en un par de segundos. No hace falta
reindexar a mano ni avisar de eso al usuario.
