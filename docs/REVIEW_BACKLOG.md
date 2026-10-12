# Review backlog (organization heuristics)

Findings parked under DECISIONS 24. Each gets a fix only when a dev
document or the clinician's acceptance run reproduces its phrase shape. All examples
are synthetic.

| Source | Finding | Direction |
|---|---|---|
| `/cr` round 26 P1 | A named org ending in an activity head with an acronym ("Oakwood Learning Strategies (OLS)") is dropped from a later comma group. | leak (constructed; 0 dev hits) |
| `/cr` round 26 P1 | A capitalized finding after a clinic comma ("Maplecrest Clinic, Severe Medical Complications") is absorbed into the name. | over-hide (0 dev hits) |
| `/cr` round 26 P2 | "Exploring Learning Classroom" becomes an unsure daycare. | over-hide (0 dev hits) |
| Advisor | `_tail_status` accepts a comma tail when any single token is an NC place that is also a word (Welcome, Faith, Union, Trinity...). | over-hide |
| Advisor | `_trim_initial` treats "-ed"/"-ing" first words as prose-shaped without checking the gazetteer ("Mildred Harper Daycare" becomes a PERSON item plus a daycare tag). | over-hide / review load |
| `/cr` round 29 P2 | A generic word in parentheses inside a name field ("Interpreter: Ana (Spanish) Salazar") is hidden with the name. | over-hide (0 dev hits for the shape) |
| Engine | "Mateo (Teo) Ruiz" in a field is one span, so the output shows "[CHILD_1])". Nothing leaks. | cosmetic |
