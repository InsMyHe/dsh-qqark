# dsh-plugin-qqark

Un plugin de DeepSeek Harness (DSH) que lleva el [archivador local de archivos de grupo y videos de chat de QQ](https://github.com/) dentro del harness: un panel lateral más cuatro herramientas de solo lectura que el agente puede invocar durante la conversación.

> Este paquete es la **mitad plugin** de un proyecto de dos partes. El archivador (`main.py`, `qqark/`, Python 3.11 puro de biblioteca estándar) vive en la raíz del repositorio; el plugin solo habla con él por `http://127.0.0.1:7801`.

## Compatibility

| Superficie | Estado |
|---|---|
| Harness | DeepSeek Harness `0.2.1-rc.5` y posteriores `0.2.x` |
| Node | `^22.19.0 \|\| >=24.0.0` |
| Plataformas | Cliente web; el lado del archivador es solo Windows (usa `explorer /select,`) |

## What it does

El archivador es un proceso residente que captura archivos de grupo y videos de chat de QQ. DSH puede estar cerrado, reiniciándose o ocupado: la captura no puede depender de él. Por eso la división es deliberada: **el archivador hace el trabajo, el plugin es un puente.**

### Arquitectura: por qué un puente y no todo dentro del plugin

- El archivador debe seguir grabando con DSH cerrado. La vida de un plugin es la del proceso del harness, así que no puede ser dueño del bucle de captura.
- El plugin habla con el archivador por su API HTTP local. No se duplica nada y no se abre ninguna base de datos desde Node.
- **Todas las herramientas expuestas al agente son de solo lectura.** Las acciones que mutan estado (escanear ahora, reintentar fallos, activar grupos, cambiar de cuenta QQ) solo están al alcance de una persona que haga clic en el panel.

### Herramientas

| Herramienta | Devuelve |
|---|---|
| `qqark_status` | estado de conexión, cuentas conectadas, si el directorio de guardado es escribible, contadores de cola y totales |
| `qqark_groups` | todos los grupos conocidos con su interruptor de captura, número de miembros y última exploración |
| `qqark_files` | archivos descargados filtrados por palabra clave / grupo / categoría / origen, con rutas locales y URL reproducibles en el navegador |
| `qqark_tasks` | cola de descargas y las tareas más recientes con el motivo del fallo |

Las cuatro degradan con elegancia: si el archivador no está en marcha devuelven una pista para lanzar `启动.bat` en lugar de un stack trace.

### Panel lateral

Un panel en el cliente web de DSH con cabecera de estado, lista de grupos con interruptores por grupo, explorador de archivos, cola de descargas, visor de registros y una tarjeta de «modo archivador» que puede arrancar el proceso y alternar NapCat entre la cuenta de archivo y la cuenta de uso diario.

### Correcciones que conviene conocer

- **`file_count` debe pasarse explícitamente.** `get_group_root_files` / `get_group_files_by_folder` de NapCat devuelven 50 elementos por defecto, y su bucle interno de paginación corta en cuanto `r.length === n.fileCount`. Un grupo grande se trunca en silencio, y ese truncamiento se propagaba a veredictos `gone` irreversibles. Solución: enviar siempre `LIST_FILE_COUNT = 1000`.
- **`gone` es un estado terminal, distinto de `failed`.** Un archivo solo se marca `gone` cuando se cumplen las tres condiciones: no se puede refrescar su `file_id`, la exploración no produjo errores y el nombre no aparece en todo el árbol. Una lectura parcial de directorios mantiene la tarea reintentable.
- **Nunca lances el archivador con `detached: true` en Windows.** En la matriz medida, todas las combinaciones con `detached: true` terminaban en menos de un segundo; lo que funciona es `cmd /c start`.
- **Vuelve a leer tras lanzar.** Que `spawn` devuelva éxito no prueba nada. El plugin sondea `/api/status` del archivador hasta que responde de verdad antes de informar éxito.
- **Las rutas del host llevan `/` inicial; el `fetch` del navegador no.** La GUI se sirve bajo `<base href="./">`, así que una ruta absoluta en `fetch` se salta el prefijo de rutas del plugin (DSH issue #1707).
- **Los hosts IPv6 conservan los corchetes.** `new URL('ws://[::1]:6200/ws').hostname` devuelve `[::1]` con corchetes; hay que quitarlos antes de comparar o sondear.

## Install

```sh
pnpm pack
dsh plugin --profile <name> add ./dsh-plugin-qqark-<version>.tgz
dsh --profile <name> --dump-config | grep 'dsh-plugin-qqark'
```

El bundle patch (`cordis.patch.yml`) se aplica automáticamente mediante `package.json#dsh.bundle.patch`. Requisitos en la máquina:

1. NapCat en marcha con una cuenta de QQ conectada
2. El archivador (`main.py`) en marcha, accesible en `http://127.0.0.1:7801`
3. `grabAccount` / `quietAccount` rellenados en los ajustes del plugin — llegan vacíos a propósito, porque un valor por defecto apuntaría en silencio a la cuenta de otra persona

## Configuration

| Clave | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `baseUrl` | string | `http://127.0.0.1:7801` | Dirección web del archivador local |
| `timeoutMs` | natural | `8000` | Tiempo de espera HTTP por petición, en milisegundos |
| `appDir` | string | `''` | Directorio del programa del archivador (el que contiene `启动.bat`); vacío = autodetección |
| `grabAccount` | string | `''` | Cuenta QQ dedicada al archivo; vacía desactiva el interruptor de modo archivador |
| `quietAccount` | string | `''` | Cuenta QQ de uso diario a la que volver; vacía desactiva el interruptor |
| `napcatConfigDir` | string | `''` | Directorio `napcat/config` de NapCat; vacío = autodetección |
| `wsPort` | natural | `6200` | Puerto que abre el archivador para el WebSocket inverso de NapCat |
| `astrbotPort` | natural | `6199` | Puerto del bot externo que el modo archivador desconecta |

La configuración la valida el esquema Schemastery `Config` de `index.js`; no hay ningún parámetro fijado en el código. **Deliberadamente no hay cuenta QQ por defecto**: el plugin se niega a activar el modo archivador hasta que ambas cuentas estén puestas, y lo dice.

## Development

```sh
pnpm install
pnpm test
```

No hay paso de compilación: el paquete entrega ESM puro (`index.js`, `client.js`, `napcat.js`) y el harness lo carga directamente. Para validar el contrato del paquete antes de publicar:

```sh
node <dsh-plugin-guide>/bin/dsh-plugin-dev.js check --cwd .
```

Estructura:

```
index.js          mitad host: esquema de config, puente HTTP, rutas de host, 4 herramientas
client.js         mitad web: componentes del panel lateral (h() a mano, sin JSX)
napcat.js         detección, arranque, login de NapCat y recarga en caliente del cliente OB11
cordis.patch.yml  bundle patch que inserta la fila del plugin
locale/           zh.json, en.json
```

## License

[MIT](../LICENSE).
