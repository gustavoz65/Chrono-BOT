# Chrono-BOT — Monitor de Admissão de Graduados da UFMT

Bot que vigia o site da UFMT e avisa no **Telegram** quando abrir um novo processo de
**Admissão de Graduados** (portadores de diploma), com foco em **Ciência da Computação — Cuiabá**.
Roda sozinho no **GitHub Actions** a cada hora, sem servidor.

## O que ele detecta

| Evento | Como detecta | Aviso |
|---|---|---|
| **Ciclo novo** no filtro "Ano" (ex.: *Admissão de Graduado 2026/2*) | lê as opções do `<select>`; se precisar, abre o navegador, escolhe a opção e clica em **Buscar** para descobrir a URL real | 🆕🚨 urgente |
| **Inscrições abertas** | extrai o período de inscrição do edital (HTML, PDF ou PDF escaneado via OCR) e compara com a data de hoje | 🚨 urgente |
| Inscrição **prevista** que **abriu hoje** | guarda as datas e avisa no dia em que abre | 🚨 urgente |
| **Últimos dias** de inscrição | faltando ≤ 2 dias | ⏰ |
| Edital, retificação, prorrogação, cronograma novo | título + conteúdo | 📢 |
| **Transferência Facultativa / Vagas Remanescentes** | ciclos e publicações novas desses processos | 📈 *sinal antecipado*: as vagas que sobram deles vão para a Admissão de Graduados |
| Resultados, chamadas, matrículas (rotina) | publicações de rotina de um ciclo em andamento | 🗂 agrupado e **sem som** |
| Fonte fora do ar ou **layout mudou** | erro HTTP ou listagem sem publicações | ⚠️ (e ✅ quando volta) |
| Monitor vivo | resumo semanal | 💓 |

Em cada aviso aparecem o status, o período de inscrição, a modalidade, o link oficial e, quando o
documento informa, **quantas vagas de Ciência da Computação em Cuiabá** existem.

O status (aberta / prevista / encerrada) é sempre calculado **pelo código**, a partir das datas.
Títulos como *"Processo Seletivo Encerrado"* também contam como sinal. O teste com o processo
atual (2026/1, inscrições de 23 a 28/06/2026) resulta em **encerrado**, como esperado.

## Arquitetura

```
ufmt_monitor/          Python: orquestração, scraping, classificação, Telegram
  monitor.py           compara com o estado anterior e decide o que avisar
  extract.py           HTML/PDF sem seletores frágeis (cards com data, menus, filtros)
  dates.py             datas em português e períodos de inscrição
  classify.py          modalidade, status, relevância, vagas de Computação
  llm.py               (opcional) Claude como segunda opinião em editais ambíguos
  browser.py / ocr.py  pontes para os módulos abaixo
browser/render.mjs     JavaScript puro + Playwright: renderiza páginas montadas por JS e
                       descobre a URL de cada opção do filtro "Ano"
ocr/ocr.c              C + API do Tesseract: lê PDFs escaneados (editais em imagem)
sources.toml           fontes monitoradas (edite aqui)
state/state.json       estado (o workflow faz commit só quando algo muda)
```

O navegador e o OCR só entram **quando necessários**. O caminho normal é HTTP simples, que é
rápido e barato. Se o navegador ou o OCR não estiverem instalados, o bot continua funcionando
sem eles.

## Colocar no ar (GitHub Actions)

1. **Criar o bot no Telegram:** fale com `@BotFather`, use `/newbot` e guarde o **token**.
2. **Descobrir seu chat_id:** mande `/start` para o seu bot e abra
   `https://api.telegram.org/bot<TOKEN>/getUpdates`. O número em `"chat":{"id":...}` é o chat_id.
   (Não compartilhe essa URL: ela contém o token.)
3. No repositório: **Settings → Secrets and variables → Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` (pode ser mais de um, separados por vírgula)
   - `ANTHROPIC_API_KEY` *(opcional: ativa a leitura de editais pelo Claude)*
4. Faça merge na branch padrão (o agendamento do GitHub só roda na branch padrão).
5. Em **Actions → Validar fontes → Run workflow**, marque *test_telegram*. Você deve receber a
   mensagem de teste, e o resumo da execução mostra o que o scraper encontrou em cada fonte:
   publicações, ciclos do filtro "Ano", URLs descobertas pelo navegador e a análise das últimas
   publicações.
6. Em **Actions → Monitor UFMT → Run workflow**, rode a primeira vez. Você recebe
   "✅ Monitor UFMT ativado" com a situação atual (2026/1 encerrado). Depois disso ele roda sozinho
   a cada hora.

### Variáveis opcionais (Settings → Variables)

| Variável | Padrão | Efeito |
|---|---|---|
| `BROWSER` | `1` | `0` desliga o navegador (execuções mais rápidas) |
| `OCR` | `1` | `0` desliga o OCR |
| `HEARTBEAT_DAYS` | `7` | dias entre mensagens "funcionando" (`0` desliga) |

**Custo do Actions:** em repositório **público** é grátis e ilimitado. Em repositório **privado**
o plano grátis tem 2.000 min/mês; cada execução leva de 1 a 2 min, então de hora em hora pode
passar do limite. Nesse caso, mude o `cron` para `7 */2 * * *` ou desligue `OCR`/`BROWSER`.

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# opcional: navegador
(cd browser && npm ci && npx playwright install chromium)
# opcional: OCR (Debian/Ubuntu)
sudo apt-get install libtesseract-dev libleptonica-dev tesseract-ocr-por poppler-utils
make -C ocr

python -m ufmt_monitor check                 # valida as fontes e mostra o que foi extraído
python -m ufmt_monitor run --dry-run         # execução completa sem enviar ao Telegram
python -m ufmt_monitor analyze <url-do-edital> --today 2026-06-25   # testa um edital
python -m ufmt_monitor test-telegram         # precisa de TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID

python -m pytest                             # testes Python (inclui OCR real se compilado)
(cd browser && npm test)                     # testes do navegador
```

## Adicionar ou ajustar fontes

Edite `sources.toml`:

- `kind = "listing"`: listagem de publicações. Toda publicação nova vira aviso, e o filtro "Ano"
  e o menu lateral são lidos para descobrir ciclos novos.
- `kind = "page"`: página genérica. Só avisa sobre links e trechos que falam de Admissão de
  Graduados, Transferência Facultativa ou Vagas Remanescentes.
- `item_pattern`: regex para restringir quais links contam como publicação (se um dia o site
  mudar e o bot começar a pegar links demais).

Depois de editar, rode **Validar fontes** para conferir.

## Limitações

- O bot só lê páginas públicas. Nunca coloque credenciais da UFMT nele.
- A classificação é conservadora, mas **confira sempre o edital oficial** antes de agir.
- Se o site mudar de estrutura, o bot avisa ("layout da página pode ter mudado") em vez de ficar
  em silêncio.
