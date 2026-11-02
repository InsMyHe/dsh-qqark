# dsh-plugin-qqark

Um plugin do DeepSeek Harness (DSH) que traz o [arquivador local de arquivos de grupo e vídeos de chat do QQ](https://github.com/) para dentro do harness: um painel lateral mais quatro ferramentas somente leitura que o agente pode chamar durante a conversa.

> Este pacote é a **metade plugin** de um projeto de duas partes. O arquivador (`main.py`, `qqark/`, Python 3.11 puro de biblioteca padrão) fica na raiz do repositório; o plugin só conversa com ele por `http://127.0.0.1:7801`.

## Compatibility

| Superfície | Estado |
|---|---|
| Harness | DeepSeek Harness `0.2.1-rc.5` e posteriores `0.2.x` |
| Node | `^22.19.0 \|\| >=24.0.0` |
| Plataformas | Cliente web; o lado do arquivador é só Windows (usa `explorer /select,`) |

## What it does

O arquivador é um processo residente que captura arquivos de grupo e vídeos de chat do QQ. O DSH pode estar fechado, reiniciando ou ocupado — a captura não pode depender dele. Por isso a divisão é deliberada: **o arquivador faz o trabalho, o plugin é uma ponte.**

### Arquitetura: por que uma ponte e não tudo dentro do plugin

- O arquivador precisa continuar gravando com o DSH fechado. A vida de um plugin é a do processo do harness, então ele não pode ser dono do laço de captura.
- O plugin fala com o arquivador pela API HTTP local. Nada é duplicado e nenhum banco é aberto a partir do Node.
- **Toda ferramenta exposta ao agente é somente leitura.** As ações que mudam estado (varrer agora, repetir falhas, ligar grupos, trocar de conta QQ) só estão ao alcance de uma pessoa clicando no painel.

### Ferramentas

| Ferramenta | Retorna |
|---|---|
| `qqark_status` | estado da conexão, contas conectadas, se o diretório de destino é gravável, contadores da fila e totais |
| `qqark_groups` | todos os grupos conhecidos com seu interruptor de captura, número de membros e última varredura |
| `qqark_files` | arquivos baixados filtrados por palavra-chave / grupo / categoria / origem, com caminhos locais e URLs reproduzíveis no navegador |
| `qqark_tasks` | fila de downloads e as tarefas mais recentes com o motivo da falha |

As quatro degradam com elegância: se o arquivador não estiver rodando, devolvem a dica de abrir `启动.bat` em vez de um stack trace.

### Painel lateral

Um painel no cliente web do DSH com cabeçalho de estado, lista de grupos com interruptores por grupo, navegador de arquivos, fila de downloads, visualizador de log e um cartão de «modo arquivador» que pode iniciar o processo e alternar o NapCat entre a conta de arquivo e a conta do dia a dia.

![Painel lateral](../docs/screenshots/real-7801.png)

### Correções que vale conhecer

- **`file_count` precisa ser passado explicitamente.** `get_group_root_files` / `get_group_files_by_folder` do NapCat devolvem 50 itens por padrão, e o laço interno de paginação para assim que `r.length === n.fileCount`. Um grupo grande é truncado em silêncio, e o truncamento cascateava em vereditos `gone` irreversíveis. Correção: enviar sempre `LIST_FILE_COUNT = 1000`.
- **`gone` é estado terminal, distinto de `failed`.** Um arquivo só é marcado `gone` quando as três condições valem: não dá para atualizar o `file_id`, a varredura não gerou erros e o nome não aparece em toda a árvore. Leitura parcial de diretórios mantém a tarefa repetível.
- **Nunca inicie o arquivador com `detached: true` no Windows.** Na matriz medida, toda combinação com `detached: true` terminava em menos de um segundo; o que funciona é `cmd /c start`.
- **Releia depois de iniciar.** `spawn` retornar sucesso não prova nada. O plugin consulta `/api/status` até o arquivador realmente responder antes de reportar sucesso.
- **Rotas do host levam `/` inicial; o `fetch` do navegador não.** A GUI é servida sob `<base href="./">`, então um caminho absoluto no `fetch` ignora o prefixo de rotas do plugin (DSH issue #1707).
- **Hosts IPv6 mantêm os colchetes.** `new URL('ws://[::1]:6200/ws').hostname` devolve `[::1]` com colchetes; remova-os antes de comparar ou sondar.

## Install

```sh
pnpm pack
dsh plugin --profile <name> add ./dsh-plugin-qqark-<version>.tgz
dsh --profile <name> --dump-config | grep 'dsh-plugin-qqark'
```

O bundle patch (`cordis.patch.yml`) é aplicado automaticamente via `package.json#dsh.bundle.patch`. Pré-requisitos na máquina:

1. NapCat rodando com uma conta QQ conectada
2. O arquivador (`main.py`) rodando, acessível em `http://127.0.0.1:7801`
3. `grabAccount` / `quietAccount` preenchidos nas configurações do plugin — vêm vazios de propósito, porque um padrão apontaria em silêncio para a conta de outra pessoa

## Configuration

| Chave | Tipo | Padrão | Descrição |
|---|---|---|---|
| `baseUrl` | string | `http://127.0.0.1:7801` | Endereço web do arquivador local |
| `timeoutMs` | natural | `8000` | Tempo limite HTTP por requisição, em milissegundos |
| `appDir` | string | `''` | Diretório do programa do arquivador (o que contém `启动.bat`); vazio = detecção automática |
| `grabAccount` | string | `''` | Conta QQ dedicada ao arquivo; vazia desativa o interruptor do modo arquivador |
| `quietAccount` | string | `''` | Conta QQ do dia a dia para a qual voltar; vazia desativa o interruptor |
| `napcatConfigDir` | string | `''` | Diretório `napcat/config` do NapCat; vazio = detecção automática |
| `wsPort` | natural | `6200` | Porta que o arquivador abre para o WebSocket reverso do NapCat |
| `astrbotPort` | natural | `6199` | Porta do bot externo que o modo arquivador desconecta |

A configuração é validada pelo esquema Schemastery `Config` em `index.js`; nenhum parâmetro está fixo no código. **Deliberadamente não há conta QQ padrão**: o plugin se recusa a ligar o modo arquivador até as duas contas estarem definidas, e diz o motivo.

## Development

```sh
pnpm install
pnpm test
```

Não há etapa de build: o pacote entrega ESM puro (`index.js`, `client.js`, `napcat.js`) e o harness carrega direto. Para validar o contrato do pacote antes de publicar:

```sh
node <dsh-plugin-guide>/bin/dsh-plugin-dev.js check --cwd .
```

Estrutura:

```
index.js          metade host: schema de config, ponte HTTP, rotas de host, 4 ferramentas
client.js         metade web: componentes do painel lateral (h() à mão, sem JSX)
napcat.js         detecção, inicialização, login do NapCat e recarga a quente do cliente OB11
cordis.patch.yml  bundle patch que insere a linha do plugin
locale/           zh.json, en.json
```

## License

[MIT](../LICENSE).
