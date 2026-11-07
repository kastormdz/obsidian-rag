# appctl

## Disco lleno

El disco duro del servidor se llena cuando los logs crecen. Para liberar espacio,
`appctl clean` purga las rotaciones viejas y deja aire en la partición raíz; si no
alcanza, `appctl prune` borra imágenes de contenedores que ya no se usan.
