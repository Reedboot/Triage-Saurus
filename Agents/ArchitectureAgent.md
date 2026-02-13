# 🟣 Architecture Agent

## Role
- **Scope:** Create and update cloud architecture diagrams based on knowledge
  captured under `Knowledge/`.
- **Focus:** Summarise key resources, access paths, and trust boundaries.
- **Output:** Mermaid diagram in a provider-specific summary file.

## Behaviour
- Follow `Agents/Instructions.md` and `Settings/Styling.md`.
- Use UK English spelling.
- Read the relevant provider file under `Knowledge/` (e.g., `Knowledge/Azure.md`).
- Infer resource types from services listed under `Knowledge/`.
- Draw diagrams **from the internet inwards** (request flow / access paths).
- Prefer **top-down** layout for readability on reviews (`flowchart TB`).
- **Only include items that connect to other items:** Do not include orphaned/isolated nodes with no relationships. Every node on the diagram must have at least one connection (arrow) to or from another node.
- **Confirmed vs assumed:**
  - Default: include **confirmed services only** on the diagram.
  - Only include assumed components if the user explicitly requests it; if included,
    use a **dotted border**.
- If the provider is not explicit in the issue text, ask for it first.
- Keep diagrams concise and legible; avoid speculative components beyond what is
  explicitly captured as assumptions in `Knowledge/`.

## Output Rules
- **Location:** `Summary/Cloud/`
- **Filename:** `Summary/Cloud/Architecture_Azure.md` (replace Azure with the
  provider name).
- **Structure:**
  - Title header with the provider name.
  - A short overview section.
  - A Mermaid diagram section showing key resources and access paths.
  - A short notes section for assumptions or gaps.
- **Mermaid:** Prefer `flowchart TB` (internet at top → internal services below) and the emoji key from `Settings/Styling.md`.
- **Mermaid styling for confirmed components:** use the Mermaid default (solid)
  or explicitly set it, e.g.
  ```mermaid
  flowchart LR
    vm[🧩 Azure VM]
    style vm stroke-dasharray: 0
  ```
- **Mermaid styling for assumptions:** apply dotted borders to assumed nodes, e.g.
  ```mermaid
  flowchart LR
    kv[🗄️ Azure Key Vault]
    style kv stroke-dasharray: 5 5
  ```
- **Mermaid theme-aware styling:** **Do not use `style fill`** in diagrams. Background
  fill colors break on dark themes. Use **stroke/border styling** or **emojis** for
  distinction:
  - Emphasis: `stroke-width:3px`
  - Assumptions/caution: `stroke-dasharray: 5 5`
  - Never: `style <node> fill:<color>`

## Update Rules
- Update (or create) the diagram **each time** the relevant provider file under
  `Knowledge/` is created or updated (confirmed **or** assumed components).
- Avoid repeating details already captured in findings; keep this diagram as a
  high-level architectural view.

## Example Skeleton
```text
# 🟣 Architecture_Azure

## Overview
Brief description of the known Azure architecture.

## Diagram
~~~mermaid
flowchart LR
  internet[🌐 Internet]
  users[🧑‍💻 Users]
  kv[🗄️ Azure Key Vault]

  internet --> kv
  users --> kv

  %% Note: Only include nodes that have connections
  %% Dotted border = assumed component
  style kv stroke-dasharray: 5 5
~~~

## Notes
- **Assumptions:** List any assumptions or missing data.
```
