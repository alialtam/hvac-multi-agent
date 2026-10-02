# How we work in this repository

Read this once before your first commit. 5 of the 45 marks are **per person** and come from
your own GitHub account: commits, the number of different days you committed, and pull
requests you opened and reviewed.

## One-time setup

1. Accept the collaborator invite (check your email or github.com/notifications).
2. Register your GitHub username on Exam Studio → Project Artifacts.
3. Make your commits count for **your** account. Use the email that is on your GitHub
   account (GitHub → Settings → Emails):

   ```bash
   git config --global user.name "Your Name"
   git config --global user.email "the-email-on-your-github-account"
   ```

4. Clone and install (see README, "Set up"):

   ```bash
   git clone https://github.com/<owner>/hvac-multi-agent.git
   cd hvac-multi-agent
   ```

5. Copy `.env.example` to `.env` and put your own keys there. `.env` is ignored by git;
   **never commit an API key**.

## Every work session

```bash
git checkout main
git pull                                  # get everyone's latest work
git checkout -b feat/<your-name>-<task>   # e.g. feat/ahmed-supervisor-graph
# ... work, run the tests ...
git add -A
git commit -m "Supervisor: route after-hours incidents to Energy first"
git push -u origin feat/<your-name>-<task>
```

Then on GitHub: **Compare & pull request** → describe what changed and how you tested it →
ask a teammate to review. After one approval, merge it and delete the branch.

- Commit when something works or at the end of each session, not once at the end of the week.
- Commit messages say what changed: `Energy agent: compute extra kWh vs baseline`, not `update`.
- Review at least one teammate pull request per day: read it, leave a real comment, approve
  or request changes. Reviews count for the reviewer.
- If `main` moved while you worked: `git checkout main && git pull && git checkout - && git merge main`.

## Rules

- Never commit `.env`, API keys, `node_modules/`, `venv/`, database files or trained models.
- Never push directly to `main`; everything goes through a pull request.
- Commit only your own work from your own account, with real dates. The guidelines treat
  commit-history manipulation and edited execution traces as academic integrity violations.
- Don't change files in `contracts/` without telling the team: the dashboard and the agents
  both depend on them.

## Where your code goes

| Folder | Owner | Read first |
| --- | --- | --- |
| `simulator/`, `backend/app/ingestion/`, `backend/app/detection/`, `dashboard/`, deployment | Ali | README |
| `backend/app/llm/`, `backend/app/agents/` (schemas, supervisor, diagnosis), execution trace | Ahmed | `backend/app/agents/README.md`, `backend/app/llm/README.md` |
| `backend/app/knowledge/`, `backend/app/agents/` (energy, maintenance), `backend/app/api/` | Abdulelah | `backend/app/knowledge/README.md`, `backend/app/api/README.md` |
| `backend/tests/` | everyone: one test file per module you own | `backend/tests/test_pipeline.py` as an example |

How the agents receive data from the detection side: `docs/HANDOVER_PERSON2.md`.
The team plan with the 7-day schedule: `docs/CA3_Team_Plan.pdf`.
