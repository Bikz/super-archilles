# Private rooms: Super Archilles for conversations that cannot leave the building

Super Archilles listens to a live conversation, keeps it on the agenda, and prepares
material while people talk: running minutes, action items, follow-up questions, and
slides. Everything runs on one Dell Pro Max GB10 on the table. No audio, transcript, or
summary is sent to a cloud API, so it works with the network cable unplugged.

That matters most in rooms where the conversation itself is the sensitive asset.

## The rooms

| Room | What the copilot does | Why it must run on the box |
|---|---|---|
| Lawyer and client intake | Keeps the intake on the checklist (conflicts, facts, deadlines, fees), writes minutes, lists issues for counsel to follow up | Privilege can be lost when confidential communications are shared with a third party. Bar guidance on generative AI (for example ABA Formal Opinion 512, 2024) asks lawyers to understand where client data goes. Many firms simply forbid pasting client matters into cloud tools. On-box inference keeps the vendor out of the room. |
| Board meetings, M&A | Tracks the agenda, records resolutions with owners and dates, flags open questions before the vote | The discussion is material non-public information. Every extra system that holds it is another leak path and another name on the insider list. Deal teams already work in clean rooms and on locked laptops; a cloud transcript service does not fit that model. |
| Government and public sector | Minutes and action tracking for closed sessions, procurement and security meetings | Data sovereignty rules and classification often require data to stay on premises or in country. Many sites are air gapped. A box that needs no network at all is the simplest thing to accredit. |
| Lectures and classrooms | Paces the lecture against the plan, saves good explanations, drafts the recap | Recordings contain student voices and questions, which are education records in many jurisdictions (FERPA in the US, GDPR in the EU), and some students are minors. Universities hesitate to send classroom audio to third parties. |
| Keynotes and sales kickoffs | Stage manager in the ear: time, drift, the next point, a recovery line | Less regulated, but unreleased products, pricing, and forecasts are discussed in rehearsal. Same box, same privacy story. |

## Who buys, and a plausible price model

- **Buyers:** law firm innovation or IT leads, general counsel offices, corporate
  secretaries and board offices, public sector IT, university teaching and learning
  centers, sales enablement teams.
- **Model:** the GB10 is a desktop workstation in the low thousands of dollars, owned by
  the customer. We sell a software license per room on top of it.
  - Professional (one room, legal or board templates, minutes export): about
    $3,000 to $6,000 per year.
  - Institution (a fleet of boxes, central template library, audit log): priced per box,
    with a discount from ten boxes, plus a setup fee for air gapped installs.
  - Education: a lower per-classroom price, sold per department.
- **Why they pay:** one avoided privilege dispute, leaked deal, or failed accreditation
  costs more than years of licenses, and the minutes alone save an associate or
  secretary an hour per meeting.

## What runs on the GB10

| Part | Model or component | Notes |
|---|---|---|
| Speech to text | NVIDIA Nemotron 3.5 ASR streaming, 0.6B | Cache-aware streaming, 80 ms frames, on the GPU |
| Language model, today | Ornith 1.5 9B, Q4_K_M GGUF (5.4 GB) | llama.cpp server, 128k context, 2 parallel slots |
| Language model, next | NVIDIA Nemotron 3.5 Bolt 74B A9B, GGUF (about 22 GB) | Mixture of experts, about 9B active per token, same API |
| Priority gate | Small local proxy in front of the LLM | The live coach always goes first; background workers wait or retry |
| Workers | Coach, live notes, slide and card workers | Separate processes that post cards to the dashboard |

**Memory:** 128 GB of unified memory shared by CPU and GPU. The 74B model weights take
about 22 GB, speech recognition a few GB, and the long-context KV cache and the app fit
in what remains, with room left over. No model is offloaded or swapped mid-meeting.

**Guarantees we can state plainly:** no outbound network calls during a session;
transcripts and notes stay on the box's disk until the customer deletes them; the same
box works in a room with no network at all.

## Limits we tell customers

- Notes are a draft for a human to approve. "Issues to flag" are prompts for counsel,
  never legal advice or conclusions.
- Speech recognition is English first today; accents and crosstalk reduce accuracy.
- Recording consent is the customer's responsibility; the copilot shows when it is
  listening.
