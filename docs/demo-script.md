# Demo script (about 4 minutes, plan: "pitch")

Setup: GB10 monitor shows `localhost:8001/stage` full screen. Presenter has `localhost:8001` (or the :8443 link on a phone)
with "pitch" picked in the dropdown. Telegram group open on a phone the judges can see. Optional second screen: `/box`.
Lines in quotes are spoken. Lines in brackets are what you do. The key words in each line are what trigger that
section's slide, so stay close to them.

## 1. Hook (0:00 to 0:30)
[Click Start talk, allow the mic.]
"Every speaker drifts and runs over. Nobody can watch their own clock and their plan at once.
So this copilot is listening to the talk right now, on the box on this table."
[Hook slide lands in the tray. Tap Show. It appears on the big screen.]

## 2. Problem and who pays (0:30 to 1:15)
"Who pays for this? Lecturers, corporate trainers, sales teams, all-hands presenters.
And classroom and meeting audio is private, so it can't go to a cloud API.
With the cloud, every answer takes about 3 seconds. On the GB10, a nudge comes back in under 1 second."
[Section slide arrives as you start; tap Show. A few seconds after the numbers, a "cloud vs GB10" comparison
slide arrives; tap Show.]

## 3. Live demo (1:15 to 2:45)
"Here's what the copilot does: it catches drift, re-plans when you fall behind, and saves good tangents.
Let me show you. This actually reminds me of my college band. We played a tiny bar in Oakland every Thursday,
the drummer was always late, and the owner had a dog that sat on the amp..."
[Keep rambling 20 to 30 seconds. A Telegram nudge arrives with a suggested line. Hold up the phone and read
the suggested line out loud; it brings you back.]
"Here's a better way to think about it: the copilot is like a GPS for a talk. It knows the route, and when you
miss a turn it reroutes instead of making you start over. And you can see the nudges arriving on Telegram and
on the dashboard as they happen."
[Point at the presenter screen: the coverage heatmap filling in, cards waiting in "Ready for the screen".
Show the Live demo slide.]

## 4. How it runs on the GB10 (2:45 to 3:30)
"All of this runs on this one box. NVIDIA's streaming speech model plus a large language model, a 74 billion
parameter Nemotron, both on the box, with smaller models preparing slides, questions and notes in the background.
Every illustration you've seen was drawn here before the talk started.
It has 128 gigabytes of unified memory, no cloud calls, and it keeps working with the wifi off."
[Section slide and a "128 GB" number slide arrive; Show one. Point at /box: GPU spikes, memory bar, network out near zero.]

## 5. Close (3:30 to 4:00)
"And the debrief arrives the moment the talk ends. Our ask: a pilot with a training team. Thank you."
[Click End talk and debrief, then Open full debrief. Point at the three numbers, the coverage map with what you
skipped, and the next plan with adjusted budgets.]

## If something lags
- No slide yet: keep talking; point at the coverage heatmap filling in instead.
- No Telegram nudge yet: keep rambling another 10 seconds; check-ins run every ~6 s and take ~3 to 4 s.
- To show it's not only for pitches: rerun with the "legal consult" plan and say a decision and an action
  item out loud ("We agreed to send the NDA by Friday, Sam owns it"); it appears in the live notes.
