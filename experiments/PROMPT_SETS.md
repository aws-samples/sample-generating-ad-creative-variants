# Ad-Blog Prompt Sets

Three prompt sets across the experiments. All use the same 4 source clips
(`samples/raw/ad/*_9s.mp4`) and the same 8 edit intents (bench_ids).

| bench_id | clip | intent |
|---|---|---|
| 805_color | fashion (805) | recolor dress |
| 805_style | fashion (805) | stylization grade |
| 44546_daypart | automotive (44546) | day -> rainy night |
| 44546_color | automotive (44546) | recolor car |
| 40766_color | sportswear (40766) | recolor top |
| 40766_season | sportswear (40766) | seasonal (autumn) |
| 4530_style | fragrance (4530) | stylization grade |
| 4530_daypart | fragrance (4530) | golden-hour relight |

---

## Set 1 — Baseline (Experiment A), hand-written editor prompts
Source: `samples/ad_prompts.json`. Fed directly to the editor (no agent).

| bench_id | prompt |
|---|---|
| 805_color | change her dress from black to a deep ruby red |
| 805_style | apply a high-fashion editorial black-and-white grade with rich contrast |
| 44546_daypart | make it a rainy downtown night with neon reflections on wet asphalt |
| 44546_color | repaint the car in glossy metallic midnight blue |
| 40766_color | change his running top to bright neon volt yellow |
| 40766_season | turn it into a crisp autumn morning run with fallen leaves on the path |
| 4530_style | grade it as a dreamy pastel fragrance film with soft glowing bloom |
| 4530_daypart | add a soft golden-hour haze with warm backlit sunlight |

## Set 2 — Nova "vague request" run (bonus samples, NOT head-to-head)
Source: `samples/ad_cases_nova.jsonl`. Deliberately vague raw user requests fed
to Nova Pro to showcase the planner's disambiguation. Outputs: `outputs/ad_nova/`.

| bench_id | vague raw request |
|---|---|
| 805_color | make the dress pop in a bold red |
| 805_style | give it a classy high-end magazine look |
| 44546_daypart | make it look like a night shoot in the city |
| 44546_color | i want the car in a cooler color |
| 40766_color | give him a brighter more energetic top |
| 40766_season | make it feel like fall |
| 4530_style | dreamy romantic perfume ad vibe |
| 4530_daypart | warmer sunset feeling |

## Set 3 — Nova HEAD-TO-HEAD run (apples-to-apples vs baseline)
Source: `samples/ad_cases_nova_h2h.jsonl`. Uses the SAME prompts as Set 1, fed to
Nova Pro as input. Isolates the effect of routing the identical instruction
through the Nova planner. Outputs: `outputs/ad_nova_h2h/`.

Prompts are identical to Set 1 (see above). Nova's refined text stays nearly
verbatim; only the assigned `subtask` differs in some cases (e.g., 805_color ->
replace_object, 40766_season -> change_background, 805_style/4530_daypart ->
add_effect). Full plans: `outputs/agent_nova_h2h/agent_pipeline_records.jsonl`.

---

## How to compare for the blog
- **Baseline vs Nova head-to-head (apples-to-apples):** Set 1 outputs
  (`outputs/ad/<...>.mp4`) vs Set 3 outputs (`outputs/ad_nova_h2h/<...>.mp4`),
  same prompt text, same editor, same CFG (2.0 / 1.0 / two-pass).
- **Planner disambiguation showcase:** Set 2 (`outputs/ad_nova/`) shows Nova
  turning vague asks into usable edits.
