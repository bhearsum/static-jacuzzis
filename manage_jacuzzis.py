#!/usr/bin/env python
import json
import os

from collections import defaultdict


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


def count_machines(machine_spec, machines):
    return len([m for m in machines if m.startswith(machine_spec)])


def get_machines(n, machine_spec, machines):
    retval = set()
    for m in machines:
        if m.startswith(machine_spec):
            retval.add(m)
            if len(retval) >= n:
                break
    return retval


def get_branch(builder):
    # XXX HARDCODING XXX
    branches = ['mozilla-inbound', 'b2g-inbound', 'birch', 'mozilla-central',
                'mozilla-aurora', 'comm-aurora', 'comm-central']
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


def allocate_builders(allocations, old_builders, builders_by_machine, max_builders_per_machine):
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
        # Don't use machines that are on different branches
        available_machines = filter_other_branch_machines(available_machines, builder, builders_by_machine)
        # Now sort by # of builders per machine so we put up to
        # max_builders_per_machine per machine
        available_machines = sorted(available_machines, key=lambda m: len(builders_by_machine[m]), reverse=True)
        new_machines = set(old_machines)
        for machine_spec, count in machines.items():
            old_count = count_machines(machine_spec, old_machines)
            delta = count - old_count
            if delta > 0:
                # Need MOAR!
                new = get_machines(delta, machine_spec, available_machines)
                new_machines.update(new)
                for m in new:
                    builders_by_machine[m].append(builder)
                #print builder, "adding", new
            elif delta < 0:
                # Don't need as many. Free up some
                unused_machines = get_machines(-delta, machine_spec, old_machines)
                new_machines -= unused_machines
                for m in unused_machines:
                    builders_by_machine[m].remove(builder)
                #print builder, "removing", unused_machines
        builders[builder] = new_machines

    return builders


def write_builders(builders, dirname):
    # First delete everything!
    for builder in os.listdir(dirname):
        fn = os.path.join(dirname, builder)
        #print "removing", fn
        os.remove(fn)

    for builder, machines in builders.items():
        fn = os.path.join(dirname, builder)
        with open(fn, 'w') as fh:
            json.dump({'machines': sorted(machines)}, fh, indent=2)


def write_machines(builders, dirname):
    # First delete everything!
    for builder in os.listdir(dirname):
        fn = os.path.join(dirname, builder)
        #print "removing", fn
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


def main():
    old_builders = load_builders("v1/builders")

    # Generate a config given existing allocations
    if False:
        config = gen_config(old_builders)
        open("config.json", "w").write(json.dumps(config, indent=2, sort_keys=True))
        exit()

    config = load_json('config.json')

    import urllib2
    all_machines = json.load(urllib2.urlopen("https://secure.pub.build.mozilla.org/builddata/reports/reportor/daily/machine_sanity/usable_slaves.json"))

    # Remove unusable machines from builders
    for builder, machines in old_builders.items():
        for m in machines[:]:
            if m not in all_machines:
                print("Removing unusable machine %s from %s" % (m, builder))
                machines.remove(m)

    #all_machines = ['bld-linux64-ec2-%03d' % i for i in range(1, 50)] + \
                   #['bld-linux64-ec2-%03d' % i for i in range(301, 350)] + \
                   #['bld-linux64-spot-%03d' % i for i in range(1, 200)] + \
                   #['bld-linux64-spot-%03d' % i for i in range(301, 500)]

    max_builders_per_machine = 2
    builders_by_machine = get_builders_by_machines(all_machines, old_builders)


    builders = allocate_builders(config['builders'], old_builders, builders_by_machine, max_builders_per_machine)

    write_builders(builders, "v1/builders")
    write_machines(builders, "v1/machines")
    write_allocated(builders, "v1/allocated")

if __name__ == '__main__':
    main()
