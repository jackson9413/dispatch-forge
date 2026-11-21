# dispatch-forge

**Local-first field service dispatcher and route planner.**

Paste a day of service calls. dispatch-forge decides who takes each job and
in what order, works out whether the customer windows can actually be met,
and then tells you what breaks when the day goes sideways.

It is local-first: the UI, the scheduling engine and the SQLite log all run on
your machine. No accounts, no API keys, no analytics, no network calls, no
model calls.

---

## Why it matters

Most dispatching is done in a spreadsheet by someone who is also driving.
The failure modes are boring and expensive:

- **A job nobody can do.** The customer needs a licensed gas fitter and the
  crew on shift does not have one. The job looks covered until 4pm.
- **A window that cannot be met.** Six calls, one van, and the third one
  closes before the van arrives.
- **One person holding the day.** The roster looks fine until the one person
  with the only licence calls in sick.
- **A day that is mostly driving.** Half the shift is in the car, and the
  route was never sequenced.
- **A plan built on guesses.** No coordinates and no drive times, so every
  arrival time is an assumption that looks exact once it is in the sheet.

dispatch-forge reads the brief, plans the day, and names those five problems
with the numbers behind them.

---

## Quick start

~~~
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 app.py
~~~

Then open <http://127.0.0.1:5000>.

Plan a brief straight from the shell:

~~~
python3 app.py samples/hvac_day.txt            # markdown plan on stdout
python3 app.py samples/hvac_day.txt --json     # the same plan as JSON
python3 app.py samples/overloaded_solo.txt --profile deadline_critical
~~~

Run the tests:

~~~
python3 tests/test_dispatch.py
~~~

---

## The brief format

A brief is plain text. Every meaningful line starts with a section tag in
square brackets. Lines starting with # are comments.

~~~
[Settings] Day: 2026-11-24 | Shift: 08:00-17:00 | Depot: 900 North Ave, Springfield IL | Loc: 39.7817, -89.6501 | Speed: 32 | Buffer: 5m | Late cost: $3

[Resource] Name: Alex Ruiz | Skills: hvac, electrical | Shift: 08:00-17:00
[Resource] Name: Sam Patel | Skills: hvac, plumbing | Shift: 08:30-16:30

[Job] ID: J1 | Customer: Rivera | Address: 12 Oak St | Loc: 39.7890, -89.6400 | Window: 09:00-11:00 | Duration: 45m | Skills: hvac | Priority: normal | Revenue: $185 | Phone: 555-0101

[Travel] From: J1 | To: J2 | Minutes: 18
~~~

### Sections

| Section | Purpose |
| --- | --- |
| [Settings] | Day, shift window, depot, speed, road factor, setup buffer, late cost, mode |
| [Resource] | One technician or crew: name, skills, shift, base |
| [Job] | One customer visit |
| [Depot] | Shorthand for the depot address |
| [Travel] | A real drive time between two stops |

### Job fields

| Field | Meaning | Notes |
| --- | --- | --- |
| ID | Job reference | Unique; used by the what-if panel |
| Customer | Who the call is for | |
| Address | Where to go | |
| Loc | Coordinates | 39.7817, -89.6501 - optional but strongly recommended |
| Window | Customer arrival window | 09:00-11:00, after 8am, by 5pm |
| Duration | On-site service time | 45m, 1h30m, 2 hours |
| Skills | Skills required | Comma separated, matched against resource skills |
| Priority | emergency, high, normal, low | Drives assignment order and lateness cost |
| Revenue | Job value | Used for coverage weighting and concentration checks |
| Phone | Contact number | |
| Notes | Anything else | |

### Settings fields

| Field | Default | Meaning |
| --- | --- | --- |
| Day | - | Label for the plan |
| Shift | 08:00-17:00 | Default shift when a resource does not set one |
| Depot | - | Start and end point for every resource |
| Speed | 32 mph | Average driving speed used for estimates |
| Road factor | 1.3 | Multiplier on straight-line distance |
| Buffer | 5m | Parking and setup friction per stop |
| Late cost | $2/min | Money value of one late minute, used in the cost model |
| Mode | team | personal makes a single-resource plan for errands and personal days |
| Assumed travel | 30m | Fallback leg time when no coordinates or [Travel] line exist |

---

## What it does

1. **Parses the brief** into jobs, resources, settings and travel overrides.
   Malformed lines are reported as warnings, never as a crash.
2. **Assigns every job** to a resource that holds the required skills and is
   on shift for the window. Each job is inserted at the position that costs
   least, then the whole plan is improved with a local search that reorders
   routes and moves jobs between crews.
3. **Walks each route** to produce arrival, start, finish, idle and lateness
   for every visit, with travel time from an operator override, a
   haversine estimate, or a stated assumption - whichever is best available,
   always labelled.
4. **Audits the result** with 28 rules. Each finding carries the evidence
   that triggered it, why it matters, and a concrete fix.
5. **Scores the day** across five axes with structural caps, so a plan
   cannot score well by skipping the hard part.
6. **Simulates a sick call** for every resource on the roster.
7. **Answers what-if questions** - add a crew, extend a shift, drop a job,
   change the assumed speed - and shows the deltas against the current plan.
8. **Exports** the plan as Markdown, CSV or JSON, and keeps a local log of
   reviewed plans with a 1-5 rating and notes.

### The rules

| Rule | Fires when |
| --- | --- |
| unassigned_job | No resource can take the job: missing skill, window outside the shift, or no feasible slot |
| window_missed | The crew arrives after the customer window closes |
| shift_overrun | The route finishes after the shift ends |
| early_window_unstaffed | A customer window opens before the earliest shift starts |
| travel_heavy | Driving takes more than 35% of the day |
| idle_gap | A resource has 20 minutes or more of idle time |
| heavy_day | A resource carries 8 or more jobs |
| capacity_overflow | Work plus travel exceeds total shift capacity |
| single_point_of_failure | Losing one resource leaves 40% or more of the day uncrewed |
| fragile_roster | More than one resource is a single point of failure |
| single_skill_holder | Only one person holds a skill that two or more jobs need |
| zero_slack | A visit starts exactly at the edge of its window |
| emergency_not_first | An emergency is behind a job that was still running when its window opened |
| priority_inversion | A higher priority job was passed over for a lower one |
| duplicate_address | Two jobs share one address |
| missing_duration | A job has no service time |
| missing_address | A job has no location at all |
| missing_window | A job has no customer window |
| missing_phone | Most jobs have no contact number |
| assumed_travel | Drive times had to be assumed because coordinates or overrides were missing |
| idle_resource | A resource finishes with nothing while another crew is busy |
| revenue_concentration | One crew carries more than 80% of the day's revenue |

### The scorecard

| Axis | What it measures |
| --- | --- |
| Window feasibility | Do the visits land inside the customer windows |
| Coverage | Is every job crewed, weighted by priority |
| Route efficiency | How much of the day is work instead of driving |
| Resilience | What happens when one resource disappears |
| Brief quality | How much of the brief is real information rather than assumption |

Structural caps: an unstaffed emergency caps coverage at 30 and the whole
review at 45. Any unstaffed job caps coverage at 55 and the review at 62.
More than a quarter of visits missing their window caps feasibility at 55.
Losing one resource and breaking half the day caps resilience at 40.

Profiles re-weight the axes: general, deadline_critical, cost_conscious,
team_day, customer_first.

---

## Samples

Six realistic briefs in samples/, all reachable from the UI:

| Sample | What it shows | Score |
| --- | --- | --- |
| hvac_day.txt | Two techs, six jobs, everything reachable | 96.5 dispatch_ready |
| bottleneck.txt | Only one person is licensed for gas work | 89.6 mostly_ready |
| no_coords.txt | A whiteboard list with no coordinates | 90.4 dispatch_ready |
| emergency_wait.txt | An emergency call and an after-hours lockout | 62.0 needs_attention |
| unknown_skill.txt | A refrigeration job nobody is qualified for | 62.0 needs_attention |
| overloaded_solo.txt | One tech, one van, far too much work | 58.2 needs_attention |

---

## API

| Method | Route | Purpose |
| --- | --- | --- |
| GET | /api/health | Liveness check |
| GET | /api/samples | The built-in sample briefs |
| POST | /api/analyze | Plan a brief, returns metrics, schedule, findings, score, resilience |
| POST | /api/scenario | Run a what-if and return the deltas |
| POST | /api/export | Markdown, CSV or JSON export of the plan |
| GET | /api/reviews | Saved review log |
| POST | /api/reviews | Save a review with a rating and notes |
| GET | /api/reviews/<id> | Fetch one saved review |

Request bodies are JSON with a brief field and an optional profile field.

---

## Tech stack

- Python 3.11+ (developed on 3.14), standard library only for the engine
- Flask 3 for the API and the static host
- SQLite for the local review log
- Vanilla HTML, CSS and JavaScript for the interface, no build step, no CDN
- 37 tests: engine, exports, API and a frontend/backend contract check

---

## Layout

~~~
app.py                  Flask app, REST API, review log, CLI
engine/models.py        Brief parsing, time and duration parsing, data models
engine/geo.py           Travel model: override, haversine, assumed
engine/schedule.py      Assignment, sequencing, local search, route evaluation
engine/rules.py         The 28 audit rules
engine/score.py         Five axis scorecard, profiles, structural caps
engine/plan.py          Orchestration, metrics, failure simulation, what-if
engine/export.py        Markdown and CSV exports
web/                    index.html, app.js, styles.css
samples/                Six realistic briefs
tests/test_dispatch.py  Test suite
~~~

---

## Limitations, stated plainly

- **No map data.** Drive times are straight-line distances inflated by a road
  factor, or an operator supplied [Travel] line, or a stated assumption. There
  is no traffic, no road network, no turn penalties.
- **The search is a heuristic.** Jobs are inserted in priority order and then
  improved with a local search. It finds good plans, not provably optimal ones.
- **Single depot, single vehicle per resource.** There is no capacity in
  cubic metres, no vehicle type and no multi-stop vehicle routing.
- **The failure simulation removes one resource at a time.** It does not model
  a partially sick crew.
- **The late cost is a default, not a fact.** $2 per late minute is a setting,
  and the score moves when you change it.

---

## Privacy

Everything stays on the machine it runs on. Customer names, addresses and
phone numbers are stored only in the local SQLite log, and only when you
press Save review. There is no telemetry in this project.
