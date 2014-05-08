#!/usr/bin/env python
"""
Write config.json specifing our jacuzzi allocations

Need to know about _running_ jobs in jacuzzis too?

Basic flow:
    - Look at past t_window hours of jobs per builder (and currently running
    jobs?). count peak # of simultaneous jobs over t_increase_threshold
    minutes.

    don't need to consider pending jobs, since that's handled by seeing a full
    jacuzzi and increasing it.

    - If we're ever more than p_increase_threshold % full for more than
    `t_increase_threshold` minutes, increase the jacuzzi

    - Otherwise, if we're less than p_decrease_threshold % full for more than
    t_decrease_threshold minutes, decrease the jacuzzi
"""
import time
import json
import logging

import sqlalchemy as sa

log = logging.getLogger(__name__)


# Global DB connection
db = None


def get_builder_activity(builder, starttime, endtime):
    """
    Yields the activity on this builder as a sequence of (time, count) tuples

    Args:
        builder(str): name of the builder to get data for
        starttime (int): timestamp of the beginning of the time range
        endtime (int): timestamp of the end of the time range

    Returns:
        A generator function that yields (time, count) tuples

        time (int): timestamp when this activitiy occurred
        count (int): how many jobs were active on this builder at this time
    """
    q = sa.text("""
                SELECT start_time, finish_time FROM buildrequests, builds WHERE
                builds.brid = buildrequests.id AND
                builds.start_time >= :starttime AND
                builds.start_time < :endtime AND
                buildername = :builder
                """)
    results = db.execute(q, builder=builder, starttime=starttime, endtime=endtime)
    results = results.fetchall()

    # We've got a list of start/end times now. We need to process them both in
    # sorted time order. For each start time, increase our count; and for each
    # end time, decrease our count
    times = [(start, 1) for (start, finish) in results]
    times.extend((finish, -1) for (start, finish) in results if finish)
    times.sort()

    count = 0
    for t, delta in times:
        count += delta
        yield t, count


def calc_builder_stats(activity, n_full, n_idle):
    """
    Calculate full and idle times for the given builder activity.

    Args:
        activity (iterable of 2-tuples): sorted tuples of (time, count)
                                         representing how many jobs were active
                                         at each point in time
        n_full (number): if we have more than this many simultaneous jobs
                         running, the allocation is considered "full" for that
                         time.
        n_idle (number): if we have less than this many simultaneous jobs
                         running, the allocation is considered "idle".
    Returns:
        (t_full, t_idle):
            t_full (float): time in seconds at or above n_full simultaneous builds
            t_idle (float): time in seconds at or below n_idle simultaneous builds
    """
    log.debug("calculating stats for n_full:%s n_idle:%s", n_full, n_idle)

    last_t = None
    last_count = None
    t_full = 0.0
    t_idle = 0.0

    for t, count in activity:
        if last_t:
            if last_count >= n_full:
                t_full += (t - last_t)
            elif last_count <= n_idle:
                t_idle += (t - last_t)

        last_t = t
        last_count = count

    return t_full, t_idle


def calc_optimal_size(activity, n0, p_increase, t_increase, p_decrease, t_decrease):
    increased = False
    n = n0
    if not activity:
        return 0

    while True:
        # How many active jobs before we think a jacuzzi is "full"
        n_increase = int(n * p_increase)
        # How many active jobs before we think a jacuzzi is "idle"
        n_decrease = int(n * p_decrease)

        t_full, t_idle = calc_builder_stats(activity, n_increase, n_decrease)

        # Re-run this until we get equilibrium
        # This means if we decrease the size by one, we'd want to increase
        # it again right away

        # If we're full more than the threshold, add more slaves
        if t_full > t_increase:
            increased = True
            n += 1

        # If we're idle more than the threshold, reduce slaves
        elif t_idle > t_decrease:
            if increased:
                break
            n -= 1

        else:
            # Looks like no changes needed!
            break

    return n


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.set_defaults(
        loglevel=logging.INFO,
    )

    parser.add_argument("-v", "--verbose", dest="loglevel", action="store_const", const=logging.DEBUG)
    parser.add_argument("-q", "--quiet", dest="loglevel", action="store_const", const=logging.WARN)
    parser.add_argument("--db", dest="db", required=True)

    args = parser.parse_args()

    logging.basicConfig(format="%(asctime)s - %(message)s", level=args.loglevel)

    global db
    db = sa.create_engine(args.db)

    # TODO: configuration for these
    now = time.time()
    yesterday = now - 86400

    p_increase = .9
    p_decrease = .5
    t_increase = 60 * 20  # 20 minutes
    t_decrease = 4 * 3600  # 4 hours

    # TODO: refactor this
    config = json.load(open("config.json"))
    changed = False
    for builder, machine_types in config['builders'].items():
        # Skip l10n for now
        if "l10n" in builder:
            continue

        n0 = sum(machine_types.values())

        # Go back an extra day to get "warmed up"
        # i.e. if we start looking at just the start time, it will appear that
        # there is 0 load until the next job starts
        activity = [
            (start, finish) for (start, finish) in
            get_builder_activity(builder, yesterday - 86400, now)
            if start >= yesterday
        ]

        # Save the current stats for later
        n_increase = int(n0 * p_increase)
        n_decrease = int(n0 * p_decrease)
        t_full0, t_idle0 = calc_builder_stats(activity, n_increase, n_decrease)

        n = calc_optimal_size(activity, n0, p_increase, t_increase, p_decrease, t_decrease)

        # Allocate at least one machine to it
        if n == 0:
            n = 1

        n_increase = int(n * p_increase)
        n_decrease = int(n * p_decrease)
        t_full, t_idle = calc_builder_stats(activity, n_increase, n_decrease)

        delta = n - n0
        if delta == 0:
            log.debug("%s OK", builder)
        else:
            log.info("%s currently %is full and %is idle", builder, t_full0, t_idle0)
            log.info("%s %+i (was %i) would result in %is full and %is idle", builder, delta, n0, t_full, t_idle)
            changed = True
            config['builders'][builder]['bld-linux64-spot-'] = max(config['builders'][builder]['bld-linux64-spot-'] + delta, 0)

    if changed:
        json.dump(config, open("config.json", "wb"), indent=2, sort_keys=True)


if __name__ == '__main__':
    main()
