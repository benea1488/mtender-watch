# mtender-watch

Monitor zilnic pentru achizițiile publice **importante** din Moldova, direct
din feed-ul OCDS al MTender (public.mtender.gov.md — fluxul operațional viu,
nu modulul analitic abandonat).

Digestul are două secțiuni: **Contracte atribuite/finalizate** (cu câștigătorii
și valoarea de atribuire) și **Licitații noi/în derulare** (cu termenul de
depunere), ambele sortate descrescător după valoare.

## Ce înseamnă „important"
`config.json`:
- `min_value_mdl` — prag bunuri/servicii (implicit 2 mln MDL)
- `min_value_works_mdl` — prag lucrări (implicit 5 mln MDL)
- `watch_buyers` — instituții urmărite indiferent de valoare
  (substring, ex. `["moldatsa", "agenția servicii publice"]`)
- `watch_cpv` — prefixe CPV urmărite indiferent de valoare (ex. `["3361"]`)

## Instalare (3 minute)
1. Repo nou pe GitHub (public = minute nelimitate la Actions), urcă fișierele.
2. Settings → Actions → General → Workflow permissions → **Read and write**.
3. Tab Actions → rulează manual `MTender daily digest` cu `lookback_days: 7`
   pentru backlog. Apoi rulează singur zilnic la 07:40 (ora Chișinăului).

Digestul apare în `output/digest_achizitii.md`; dedup în `data/seen.json`
(o procedură re-apare o singură dată per etapă: o dată ca licitație,
o dată ca atribuire).

## Limite cunoscute
- Fără date de **execuție/plăți** — API-ul MTender se oprește la contract.
- Câmpul `awards` poate lipsi la unele proceduri (structura multi-record a
  MTender); acestea apar ca „finalizate" fără câștigător — verifică pe portal.
- Achizițiile de valoare mică raportate agregat pot avea metadate incomplete.
- Prima rulare reală calibrează: dacă `Erori la fetch` e mare, crește
  `request_pause` în `mtender_watch.py`.
