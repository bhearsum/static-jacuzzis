#!/usr/bin/env python
import json
import os
import urllib2

from collections import defaultdict

import logging
log = logging.getLogger(__name__)

# Where the list of usable slaves lives
USABLE_SLAVES = "https://secure.pub.build.mozilla.org/builddata/reports/reportor/daily/machine_sanity/usable_slaves.json"


def load_json(filename):
    return json.load(open(filename))


def load_builders(dirname):
    retval = {}
    for builder in os.listdir(dirname):
        fn = os.path.join(dirname, builder)
        retval[builder] = load_json(fn)['machines']
    return retval


def get_builders_by_machines(all_machines, builders):
    retval = defaultdict(list)
    for m in all_machines:
        retval[m] = []

    for builder, machines in builders.iteritems():
        for m in machines:
            retval[m].append(builder)

    return retval


def get_machines_by_spec(machines, machine_spec):
    retval = set()
    for m in machines:
        if m.startswith(machine_spec):
            retval.add(m)
    return retval


def count_machines(machines, machine_spec):
    return len(get_machines_by_spec(machines, machine_spec))


def get_machines(n, machines, machine_spec):
    retval = set()
    for m in get_machines_by_spec(machines, machine_spec):
        if check_slavealloc(m):
            retval.add(m)
        if len(retval) >= n:
            break
    return retval


def get_branch(builder):
    # XXX HARDCODING XXX
    branches = ['mozilla-inbound', 'b2g-inbound', 'birch', 'mozilla-central',
                'mozilla-aurora', 'comm-aurora', 'comm-central', 'fx-team']
    for b in branches:
        if b in builder:
            return b
    else:
        raise ValueError("Couldn't determine branch for %s" % builder)


def filter_other_branch_machines(machines, builder, builders_by_machine):
    this_branch = get_branch(builder)
    # Now remove any machines that have allocations not on this branch
    retval = []
    for m in machines:
        for b in builders_by_machine[m]:
            if get_branch(b) != this_branch:
                break
        else:
            retval.append(m)
    return retval


def allocate_builders(allocations, old_builders, builders_by_machine,
                      max_builders_per_machine):
    # For builders that used to have allocations but don't any more, free up
    # their machines
    for builder in set(old_builders.keys()) - set(allocations.keys()):
        for m in old_builders[builder]:
            builders_by_machine[m].remove(builder)

    # For each allocation, figure out if we need more or fewer machines
    builders = {}
    for builder, machines in allocations.items():
        if not builder in old_builders:
            old_machines = []
        else:
            # For each machine type, count how many we have
            old_machines = old_builders[builder]

        available_machines = set(m for (m, bl) in builders_by_machine.items() if len(bl) < max_builders_per_machine)
        # Don't use machines already allocated to this builder
        if builder in old_builders:
            available_machines -= set(old_builders[builder])

        # Don't use machines that are on different branches
        available_machines = filter_other_branch_machines(
            available_machines, builder, builders_by_machine)
        # Now sort by # of builders per machine so we put up to
        # max_builders_per_machine per machine
        available_machines = sorted(available_machines, key=lambda m: len(builders_by_machine[m]), reverse=True)
        new_machines = set(old_machines)
        for machine_spec, count in machines.items():
            # Ignore these, they're special
            old_count = count_machines(old_machines, machine_spec)
            delta = count - old_count
            if delta > 0:
                # Need MOAR!
                new = get_machines(delta, available_machines, machine_spec)
                new_machines.update(new)
                for m in new:
                    builders_by_machine[m].append(builder)
                log.debug("%s adding %s", builder, new)
            elif delta < 0:
                # Don't need as many. Free up some
                unused_machines = get_machines(-delta, old_machines, machine_spec)
                new_machines -= unused_machines
                for m in unused_machines:
                    builders_by_machine[m].remove(builder)
                log.debug("%s removing %s", builder, unused_machines)
        if len(new_machines) == 0:
            raise ValueError("No machines allocated for %s" % builder)
        builders[builder] = new_machines

    return builders


def write_builders(builders, dirname):
    # First delete everything!
    for builder in os.listdir(dirname):
        fn = os.path.join(dirname, builder)
        os.remove(fn)

    for builder, machines in builders.items():
        fn = os.path.join(dirname, builder)
        with open(fn, 'w') as fh:
            json.dump({'machines': sorted(machines)}, fh, indent=2)


def write_machines(builders, dirname):
    # First delete everything!
    for builder in os.listdir(dirname):
        fn = os.path.join(dirname, builder)
        os.remove(fn)

    builders_by_machine = defaultdict(list)
    for builder, machines in builders.items():
        for m in machines:
            builders_by_machine[m].append(builder)

    for machine, machine_builders in builders_by_machine.items():
        fn = os.path.join(dirname, machine)
        with open(fn, 'w') as fh:
            json.dump({'builders': sorted(machine_builders)}, fh, indent=2)


def write_allocated(builders, dirname):
    all_machines = set()
    for builder, machines in builders.items():
        all_machines.update(machines)

    fn = os.path.join(dirname, "all")
    with open(fn, 'w') as fh:
        json.dump({'machines': sorted(all_machines)}, fh, indent=2)


def gen_config(old_builders):
    "generate config given existing allocations"
    builders = {}
    for builder, machines in old_builders.items():
        ec2 = 0
        spot = 0
        for m in machines:
            if "-ec2-" in m:
                ec2 += 1
            elif "-spot-" in m:
                spot += 1
        builders[builder] = {
            'bld-linux64-ec2-': ec2,
            'bld-linux64-spot-': spot,
        }

    return {"builders": builders}


SLAVEALLOC_URL = "http://slavealloc.pvt.build.mozilla.org/api"


_trustlevelCache = {}


def get_trust(trustid):
    if trustid in _trustlevelCache:
        return _trustlevelCache[trustid]

    url = "{0}/trustlevels/{1}".format(SLAVEALLOC_URL, trustid)
    _trustlevelCache[trustid] = json.load(urllib2.urlopen(url))['name']
    return _trustlevelCache[trustid]


_envCache = {}


def get_environ(envid):
    if envid in _envCache:
        return _envCache[envid]

    url = "{0}/environments/{1}".format(SLAVEALLOC_URL, envid)
    _envCache[envid] = json.load(urllib2.urlopen(url))['name']
    return _envCache[envid]


def check_slavealloc(m):
    """Returns True if this machine is enabled, and is in the prod environ, and
    has 'core' trust"""
    url = "{0}/slaves/{1}?byname=1".format(SLAVEALLOC_URL, m)
    try:
        result = json.load(urllib2.urlopen(url))
        trust = get_trust(result['trustid'])
        env = get_environ(result['envid'])
        log.debug("%s - %s %s %s", m, result['enabled'], trust, env)
        if result['enabled'] and trust == 'core' and env == 'prod':
            return True
        return False
    except Exception:
        log.exception("Couldn't check slavealloc")
        return False


def get_usable_slaves(config):
    all_machines = json.load(urllib2.urlopen(USABLE_SLAVES))
    machine_specs = set()
    for builder_config in config['builders'].values():
        machine_specs.update(builder_config.keys())

    # Filter all machines by only those matching our specs
    matching_machines = set()
    for s in machine_specs:
        matching_machines.update(get_machines_by_spec(all_machines, s))

    return matching_machines


def main():
    logging.basicConfig(format="%(asctime)s - %(message)s", level=logging.INFO)

    old_builders = load_builders("v1/builders")

    # Generate a config given existing allocations
    if False:
        config = gen_config(old_builders)
        open("config.json", "w").write(json.dumps(config, indent=2, sort_keys=True))
        exit()

    config = load_json('config.json')

    all_machines = get_usable_slaves(config)

    # Remove unusable machines from builders
    for builder, machines in old_builders.items():
        for m in machines[:]:
            if m not in all_machines:
                log.debug("Removing unusable machine %s from %s", m, builder)
                machines.remove(m)
            elif not check_slavealloc(m):
                log.debug("Removing machine %s from %s due to slavealloc", m, builder)
                machines.remove(m)

    max_builders_per_machine = 2
    builders_by_machine = get_builders_by_machines(all_machines, old_builders)

    builders = allocate_builders(config['builders'], old_builders,
                                 builders_by_machine, max_builders_per_machine)

    write_builders(builders, "v1/builders")
    write_machines(builders, "v1/machines")
    write_allocated(builders, "v1/allocated")

if __name__ == '__main__':
    main()
