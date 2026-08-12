"""Decide which channels to create from provider streams that have none.

Pure and Django-free so it can be unit tested outside the Dispatcharr runtime.
The caller supplies the stream rows, the channel names already in use, and a
resolver that turns a stream name into a proposed channel name.

The unit of work is a STATION, not a stream. This provider carries the same
station once per M3U account, so four rows share one name, and the layout on
this installation is one channel per station holding all four streams. That is
why the existing Import M3U Streams action is not the right tool here: it
creates one channel per stream and disambiguates them with a suffix, which
gives four channels for one station.

Attaching streams to the channels is deliberately not done here. A channel
created from this plan is a target for the stream matcher that already exists
for that job.
"""
import collections

SeedItem = collections.namedtuple(
    "SeedItem", "proposed_name source_names stream_ids accounts")
SeedPlan = collections.namedtuple("SeedPlan", "create skip unresolved")


def build_seed_plan(streams, existing_names, resolve):
    """Group streams by the channel name they resolve to and classify each group.

    ``streams`` is a list of dicts carrying ``id``, ``name`` and
    ``m3u_account_id``. ``existing_names`` is the channel names already in the
    database, compared without regard to case or surrounding space.
    ``resolve`` takes a stream name and returns a proposed channel name or None.

    Returns a SeedPlan whose ``create`` list holds names not already in use,
    ``skip`` holds names a channel already carries, and ``unresolved`` holds one
    item per stream name that resolved to nothing. Nothing is dropped silently:
    a name that resolves to nothing is reported so the operator can see what was
    not understood.

    Ordering is by proposed name, so two runs over the same data plan the same
    work in the same order regardless of the order the database returned rows.
    """
    taken = {str(name).strip().lower() for name in (existing_names or [])}

    by_name = collections.OrderedDict()
    unresolved = collections.OrderedDict()
    for stream in sorted(streams or [],
                         key=lambda s: ((s.get("name") or ""), s.get("id") or 0)):
        source_name = (stream.get("name") or "").strip()
        if not source_name:
            continue
        proposed = resolve(source_name)
        bucket = by_name if proposed else unresolved
        key = proposed if proposed else source_name
        entry = bucket.setdefault(
            key, {"source_names": [], "stream_ids": [], "accounts": []})
        if source_name not in entry["source_names"]:
            entry["source_names"].append(source_name)
        entry["stream_ids"].append(stream.get("id"))
        entry["accounts"].append(stream.get("m3u_account_id"))

    create, skip = [], []
    for proposed in sorted(by_name):
        entry = by_name[proposed]
        item = SeedItem(proposed_name=proposed,
                        source_names=entry["source_names"],
                        stream_ids=entry["stream_ids"],
                        accounts=entry["accounts"])
        (skip if proposed.strip().lower() in taken else create).append(item)

    unresolved_items = [
        SeedItem(proposed_name=None,
                 source_names=[name],
                 stream_ids=unresolved[name]["stream_ids"],
                 accounts=unresolved[name]["accounts"])
        for name in sorted(unresolved)
    ]
    return SeedPlan(create=create, skip=skip, unresolved=unresolved_items)


def allocate_channel_numbers(used, count, start=None):
    """Return ``count`` free channel numbers in ascending order.

    Channel numbers are unique across this installation, so an allocator that
    reused one would fail the write rather than quietly produce a duplicate.
    There is not always a contiguous free block either: when 135 channels were
    created by hand, the largest gap beside the existing block was 76. So the
    numbers already in use are skipped rather than assumed away.
    """
    taken = {float(value) for value in (used or []) if value is not None}
    if count <= 0:
        return []
    if start is None:
        start = (max(taken) + 1.0) if taken else 1.0
    numbers = []
    candidate = float(start)
    while len(numbers) < count:
        if candidate not in taken:
            numbers.append(candidate)
            taken.add(candidate)
        candidate += 1.0
    return numbers
