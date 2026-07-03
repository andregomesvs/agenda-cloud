# Agenda Consolidada — versão cloud (Render + Firestore + login Google)

Mesma agenda + Kanban de antes, agora rodando na nuvem 24/7, sem depender
do seu PC e sem arquivo local pra travar. Só você (o e-mail que você
definir) consegue acessar.

Arquitetura:
- **Render.com** — hospeda o servidor Python (grátis, sem cartão)
- **Firestore** (Firebase) — guarda tarefas, configuração e o cache dos
  eventos (grátis, plano Spark)
- **Firebase Authentication (Google)** — login; o servidor confere o
  e-mail em toda requisição, não só a tela

Nenhum desses passos custa nada, mas todos são feitos manualmente pelos
sites — eu não consigo criar as contas por você.

---

## Parte 1 — Firebase (10 min)

1. Acesse **console.firebase.google.com** → **Criar projeto** → dê um
   nome (ex: `agenda-consolidada`) → pode desativar o Google Analytics
   (não precisa) → Criar.

2. **Autenticação:** menu lateral → **Authentication** → **Sign-in
   method** (ou "Get started" na primeira vez) → habilite o provedor
   **Google** → salve.

3. **Banco de dados:** menu lateral → **Firestore Database** → **Criar
   banco de dados** → modo **produção** → escolha uma região (ex:
   `southamerica-east1` se existir, ou `us-central1`) → Ativar.

4. **App Web (pega as chaves públicas do front-end):** ⚙️ **Configurações
   do projeto** → aba **Geral** → role até "Seus apps" → ícone **`</>`**
   (Web) → dê um nome (ex: `agenda-web`) → **não** precisa marcar
   Hosting → Registrar app. Vai aparecer um bloco `firebaseConfig` — guarde
   os valores de `apiKey`, `authDomain`, `projectId` e `appId`, você vai
   colar no Render daqui a pouco.

5. **Credencial do servidor (chave privada):** mesma tela de
   Configurações → aba **Contas de serviço** → **Gerar nova chave
   privada** → confirma → baixa um arquivo `.json`. **Esse arquivo é
   secreto** — não sobe pro GitHub, não compartilha. Vai direto pra uma
   variável de ambiente no Render (próximo passo).

---

## Parte 2 — Subir o código pro GitHub

O Render precisa de um repositório Git pra fazer o deploy.

```bash
cd agenda-cloud
git init
git add .
git commit -m "agenda consolidada - versão cloud"
```

Cria um repositório **privado** novo no GitHub (github.com → New
repository → Private) e sobe:

```bash
git remote add origin https://github.com/SEU-USUARIO/agenda-consolidada.git
git branch -M main
git push -u origin main
```

O `.gitignore` já vem configurado pra nunca subir a chave do Firebase
por acidente.

---

## Parte 3 — Render (5 min)

1. **render.com** → criar conta (dá pra usar login do GitHub) → **New**
   → **Web Service** → conecta o repositório que você acabou de criar.

2. Configuração do serviço:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python app.py`
   - **Instance Type:** Free

3. **Environment** → adicione as variáveis:

   | Nome | Valor |
   |---|---|
   | `ALLOWED_EMAIL` | seu e-mail do Google (ex: `andre@gmail.com`) |
   | `FIREBASE_SERVICE_ACCOUNT_JSON` | cole o **conteúdo inteiro** do arquivo `.json` da Parte 1, passo 5 |
   | `FIREBASE_WEB_API_KEY` | o `apiKey` da Parte 1, passo 4 |
   | `FIREBASE_AUTH_DOMAIN` | o `authDomain` da Parte 1, passo 4 |
   | `FIREBASE_PROJECT_ID` | o `projectId` da Parte 1, passo 4 |
   | `FIREBASE_APP_ID` | o `appId` da Parte 1, passo 4 |

   (o Render já define `PORT` sozinho, não precisa mexer)

4. **Create Web Service** → aguarda o build e o deploy (2-5 min). No
   final você recebe uma URL tipo `https://agenda-consolidada.onrender.com`.

5. **Autoriza o domínio no Firebase:** volta no Firebase Console →
   Authentication → **Settings** → **Authorized domains** → **Add
   domain** → cola o domínio do Render (sem `https://`, ex:
   `agenda-consolidada.onrender.com`). Sem isso o login com Google falha.

---

## Parte 4 — Usar

1. Abre a URL do Render → **Entrar com Google** → usa a conta do e-mail
   que você colocou em `ALLOWED_EMAIL`. Qualquer outra conta é recusada.
2. Configura os dois links `.ics` normalmente (mesma tela de antes,
   "Configurar contas e link .ics").
3. Pronto — acessa de qualquer lugar, PC ou celular, sem precisar do seu
   computador ligado.

---

## Sobre o plano gratuito do Render "dormir"

O plano free do Render desliga o serviço depois de ~15 min sem receber
requisições, e demora uns 30-50s pra acordar na próxima visita. Isso
também pausa a sincronização automática enquanto estiver dormindo.

Se quiser sincronização contínua mesmo sem você abrir a página, configure
um "pinger" gratuito pra visitar o app a cada 5-10 min:

- **UptimeRobot** (uptimerobot.com, grátis) ou **cron-job.org** (grátis)
- Aponte pra: `https://SEU-APP.onrender.com/healthz` (essa rota não
  precisa de login, é só um "estou vivo")

Isso mantém o servidor sempre acordado e a agenda sempre atualizada.

---

## Segurança

- A verificação de acesso acontece **no servidor** (`verify_id_token` +
  comparação de e-mail), não só na tela — mesmo que alguém ache a URL,
  só entra com o e-mail autorizado.
- O `apiKey` do Firebase que fica visível no navegador **não é segredo**
  (é assim que o Firebase Web SDK funciona); a proteção de verdade é a
  chave de serviço (`FIREBASE_SERVICE_ACCOUNT_JSON`), que fica só no
  Render, nunca no navegador.
- Se quiser trocar o e-mail autorizado depois, é só editar a variável
  `ALLOWED_EMAIL` no Render (aba Environment) e o serviço reinicia
  sozinho.

## Arquivos

- `app.py` — servidor Flask + Firestore + verificação de login
- `static/index.html` — dashboard (calendário + Kanban + tela de login)
- `requirements.txt` — dependências Python
