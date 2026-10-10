[← Documentation](README.md)

# Statistics

For what each number means and how it is measured (including the limits of the CPU figure), see [Metrics](metrics.md).

**Statistics** in the navigation shows how one server behaved over time. Pick a server and a
range (1 hour, 6 hours, 24 hours, 7 days, 30 days, 90 days, 1 year or all time) and you get
CPU, memory, swap, load and root disk charts. Each chart is the average as a line with a
lighter band up to the highest value, so a short spike stays visible even in a yearly view.
Hover or touch a point for the exact time, average, highest value and number of check-ins, or
use **View as table**. Gaps (a server that was offline) are drawn as gaps. A note under the
cards says what each point is (for example "one per hour, from hourly summaries") and when
the history starts. The selection is kept in the address, so a view can be bookmarked, and
the range is remembered per browser. The short ranges refresh every 30 seconds, except
while the tab is hidden.

Under the charts, **Containers** shows every container of that server as a small card (status
dot, health, restarts, CPU, memory against its limit), with search and sorting (problems first
by default). Select a card for that container's CPU and memory over the chosen range, with its
memory limit drawn as a dashed line, how much of the time it was not running, and how many
times it restarted in the range. A container only reports CPU and memory while it runs.

Further down a server's page: how its CPU and memory use were **distributed** (the share of the
time in each 10% band), **when it is busy** (average CPU or memory by hour of the day and weekday
over the last 30 days, in your time zone), **storage** (a chart per filesystem with an estimate
such as "full in about 3 days" when there is a clear growth trend, and none otherwise), and
**reliability** (how long Docker takes to list containers, and how long the collector takes).

Choose **All servers** for the whole fleet: containers by state and open incidents by severity
(donuts), incidents opened over time by severity (stacked columns), what the incidents were
about, servers by disk usage, the containers using the most memory and CPU right now, and each
server's availability. Availability is the share of expected check-ins that arrived; it is
approximate because it assumes each server's current check interval. Every chart can be shown
as a table.

Short ranges come from the stored check-ins, longer ones from the summaries described under
[Database schema](database.md), so a year of history costs about as much to load as an
hour.

What to know about the statistics:

- **Time zones.** Charts for up to a day or two use your clock; day, week (Monday to Sunday) and
  month points are aligned to UTC and labelled in UTC, because that is how they are summarised.
- **Some figures are approximate**: distributions and the days-until-full estimate are worked out
  in the browser from the points you are looking at, availability assumes each server's current
  check interval, and the top containers show values from the latest check-in, not an average
  over the range. The page says so where it matters.
- **Pointer-only details.** Hover and touch tooltips don't work from the keyboard yet; every chart
  has **View as table** as an alternative.
- **Not built yet** (tracked in Milestone 8 on GitHub): CSV and JSON export, reboots per month, a
  state timeline and restarts per day for containers, rankings over the chosen range, network
  traffic charts, event markers on charts, and retention settings for the summaries.
