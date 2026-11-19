# Alerta de passagens baratas ✈️🔔

Busca preços no Google Flights (que junta GOL, Azul, LATAM e agências), guarda o histórico
e te avisa no Telegram quando a passagem fica abaixo do preço que você quer ou cai bastante.

## O site (Vercel, grátis)

A pasta `public/` é o site (pesquisar passagens, ver preço por dia, criar e apagar avisos) e `api/app.py`
é o servidor. Hospedagem na Vercel, ligada a este repositório. Variáveis de ambiente do projeto na Vercel:

| Nome | O que é |
|---|---|
| `SERPAPI_KEY` | chave do SerpApi |
| `SITE_SENHA` | uma senha sua, pra ninguém mais usar o site (e gastar suas buscas) |
| `GITHUB_REPO` | `kalksvitor-cloud/Alerta-passagem` |
| `GITHUB_ALERTAS_TOKEN` | token do GitHub com permissão de escrita em *Contents* só deste repositório |

Os avisos criados no site são gravados no `alertas.json` deste repositório, e o robô do GitHub Actions
confere e manda no Telegram.

## 1. O que você precisa criar (uns 10 minutos, tudo grátis)

**a) Bot do Telegram**
1. No Telegram, procure **@BotFather** e mande `/newbot`.
2. Escolha um nome (ex.: "Alerta Passagens Vitor") e um usuário terminado em `bot` (ex.: `alerta_passagens_vitor_bot`).
3. Ele te devolve um **token** parecido com `123456789:ABCdef...`. Guarde.
4. Abra o seu bot novo e mande um "oi" pra ele (isso é necessário pro passo 3).

**b) Conta no SerpApi** (é quem consulta o Google Flights)
1. Crie conta em https://serpapi.com (plano Free).
2. Em https://serpapi.com/manage-api-key copie sua **API key**.
3. O plano grátis tem um limite de buscas por mês (veja no painel). Cada data pesquisada gasta 1 busca;
   uma faixa de 10 dias gasta 10 buscas por verificação.

## 2. Rodar no seu computador

Precisa do Python 3.10+ (https://www.python.org/downloads, marque "Add to PATH" no Windows). Não tem outras dependências.

1. Copie `.env.exemplo` para `.env` e cole o token e a chave.
2. Descubra seu chat id: `./iniciar.sh chatid` (Windows: `iniciar.bat chatid`) e cole o número no `.env`.
3. Ligue o bot: `./iniciar.sh bot` (Windows: `iniciar.bat bot`).

Enquanto ele estiver ligado, você conversa com ele no Telegram e ele verifica seus alertas a cada 6 horas.

## 3. Comandos no Telegram

| Comando | O que faz |
|---|---|
| `/buscar bh curitiba 15/01/2027` | Mostra os voos mais baratos desse dia |
| `/buscar bh curitiba 10/01/2027:20/01/2027` | Acha o **dia** mais barato na faixa (até 15 dias) |
| `/buscar bh curitiba 15/01/2027 20/01/2027` | Ida e volta |
| `/alertar bh curitiba 15/01/2027 400` | Avisa quando ficar até R$ 400 |
| `/lista` | Seus alertas |
| `/remover 1` | Apaga o alerta 1 |
| `/verificar` | Verifica todos os alertas agora |

Pode usar o nome da cidade (bh, sp, rio, curitiba, salvador...) ou o código do aeroporto (CNF, CWB, GRU...).

**Quando chega alerta:** preço abaixo do seu alvo (só avisa de novo se baixar mais), queda de 10% ou mais
desde a última busca, ou o menor preço já visto para aquela rota.

## 4. (Opcional) Rodar sozinho na nuvem, de graça, sem deixar o PC ligado

1. Crie um repositório **privado** no GitHub e suba estes arquivos.
2. Em *Settings → Secrets and variables → Actions*, crie `SERPAPI_KEY`, `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`.
3. O GitHub vai verificar às 8h e às 20h e mandar os alertas. Nesse modo você cadastra alertas editando o
   `alertas.json` direto no GitHub (o bot interativo só funciona quando roda no PC).

## Uso pelo terminal (sem Telegram)

```
python3 passagens.py buscar CNF CWB 2027-01-15
python3 passagens.py adicionar CNF CWB 2027-01-15 --max 400
python3 passagens.py verificar
```
Sem `SERPAPI_KEY` o programa usa **dados de exemplo** (preços falsos), só pra você testar.
