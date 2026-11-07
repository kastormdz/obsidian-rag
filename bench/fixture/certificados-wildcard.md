# Certificados wildcard

Para renovar un certificado vencido se corre certbot con el plugin de DNS. El
certificado wildcard cubre todos los subdominios y los certificados vencen a los 90
días, así que el timer renueva antes de que expiren.
