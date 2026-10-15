# Florida Man of the Day

**One real headline a day, from the newsroom that reported it.**

👉 **[mocksea.github.io/florida-man](https://mocksea.github.io/florida-man/)**

Every morning this site picks a Florida Man story that happened on today's date in a past year and puts it on the front page. Not a meme, not a template. An actual headline from an actual local paper, with a link back to them.

Today it might be a man arrested for riding a manatee. Tomorrow, someone defending his yard with a pool noodle. The state does the writing. We just pick.

## Things you can do

- **Read today's story.** It's the big headline. That's the whole job.
- **Swipe through the runners-up.** Most days have more than one contender. The arrows next to "1 of 10" flip through them.
- **Look up your birthday.** Put in any date and see what Florida Man was up to. This is the traditional use of the internet and we respect it.
- **Narrow it to a town.** Tap "Town" and start typing. 475 places are in there, cities and towns from Alachua to Zolfo Springs plus the unincorporated spots that keep turning up in police reports.
- **Roll the dice.** "Random story" in the top corner pulls one from the archive.
- **Share it.** Every story has its own link, so you can send one to the group chat without screenshotting.

## How it picks

Every story gets read and scored, mostly on how *Florida* it is. Alligators, manatees, lawnmowers, a weapon that isn't a weapon ("attacked with a burrito"): points. A story that's actually a tragedy gets pushed way down, because somebody's worst day isn't the punchline. The top scorer wins the day, and once a date is picked it stays picked.

The summary under each headline is the one-line blurb the publisher wrote for link previews. We show that sentence and send you to them for the rest. No articles copied, no AI writing anything, no ads, no tracking.

## Where the stories come from

Google News, searched for "Florida man" on that date. A robot does the rounds every morning, saves the day's finds, and the archive grows by a day each time. If your birthday comes up empty, it just hasn't been collected yet. Check back.

## Fine print

Everyone in these stories is a real person, and most of them were having a very bad day. They're innocent until proven guilty, and the headline is the newsroom's, not ours. Filed under Florida, allegedly.

Built by Moxy, with design notes from a guy who said "this UX is doodoo" and was right.

---

### For the curious

The site is a handful of plain files with no server behind it. The code that finds, scores and summarizes stories is Python with no outside libraries, and the same code also runs as a small local server. [How it works](docs/how-it-works.md) covers the details: scoring, the daily pick, the summary fetcher and the security around it.

**Run it yourself:**

```sh
python3 -m fm                 # local server on http://127.0.0.1:7510
python3 -m fm.build --out site   # build the static site into site/
python3 -m unittest discover -s tests -t .
```
