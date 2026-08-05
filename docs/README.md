# Channel Mapparr documentation

Six pages, split by who is reading.

## If you are running Dispatcharr

**[User guide](USER-GUIDE.md)** is the one you want. It covers a first run, what
Dry Run Mode actually changes, scoping which channels are touched, how broadcast
station names are built, what to do when a channel will not match, reading a
report, where every file is written, and a troubleshooting section arranged by
symptom. It also holds the complete settings and action reference.

**[Changelog](CHANGELOG.md)** lists what changed in each version, described in
terms of what you will notice rather than which functions moved.

## If you are working on Channel Mapparr itself

**[Development notes](DEVELOPMENT.md)** cover the runtime model, which is the
first thing to understand: the plugin runs inside Dispatcharr's Django backend
and cannot be run standalone. It also covers what each test file pins, the
automation that guards edits, how to edit the channel databases, the release
procedure, and the gotchas that were learned the hard way.

**[Open work](TODO.md)** is the backlog: what is done, what is planned, and the
measurements behind each item.

## If you are reading the matcher

**[Migration guide](MIGRATION_GUIDE.md)** holds the Django ORM patterns and the
pitfalls behind them. The plugin was migrated from an HTTP API design, and this
page is why the data access looks the way it does. Do not reintroduce HTTP calls
for data access.

**[Matcher normalization port](MATCHER-NORMALIZATION-PORT.md)** records a
hand-port of name-normalization fixes between sibling plugins. It is superseded:
those primitives now live in a shared core that is vendored into each plugin.
Kept because it explains why the code is shaped as it is.

---

The **[project front page](../README.md)** describes what the plugin is and what
it does, with screenshots.
