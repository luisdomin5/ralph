# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import re
import datetime

from django.template import loader, Context
from powerdns.models import Domain, Record

from lck.django.common import nested_commit_on_success
from lck.django.common.models import MACAddressField

from ralph.dnsedit.models import DHCPEntry



HOSTNAME_CHUNK_PATTERN = re.compile(r'^([A-Z\d][A-Z\d-]{0,61}[A-Z\d]|[A-Z\d])$',
                                    re.IGNORECASE)


def is_valid_hostname(hostname):
    """Check if a hostname is valid"""
    if len(hostname) > 255:
        return False
    hostname = hostname.rstrip('.')
    return all(HOSTNAME_CHUNK_PATTERN.match(x) for x in hostname.split("."))


def clean_dns_name(name):
    """Remove all entries for the specified name from the DNS."""
    name = name.strip().strip('.')
    for r in Record.objects.filter(name=name):
        r.delete()


def clean_dns_address(ip):
    """Remove all A entries for the specified IP address from the DNS."""
    ip = str(ip).strip().strip('.')
    for r in Record.objects.filter(content=ip, type='A'):
        r.delete()


def add_dns_address(name, ip):
    """Add a new DNS record in the right domain."""
    name = name.strip().strip('.')
    ip = str(ip).strip().strip('.')
    host_name, domain_name = name.split('.', 1)
    domain = Domain.objects.get(name=domain_name)
    record = Record(
        domain=domain,
        name=name,
        type='A',
        content=ip,
    )
    record.save()


@nested_commit_on_success
def reset_dns(name, ip):
    """Make sure the name is the only one pointing to specified IP."""
    clean_dns_name(name)
    clean_dns_address(ip)
    add_dns_address(name, ip)


def clean_dhcp_mac(mac):
    """Remove all DHCP entries for the given MAC."""
    mac = MACAddressField.normalize(mac)
    for e in DHCPEntry.objects.filter(mac=mac):
        e.delete()


def clean_dhcp_ip(ip):
    """Remove all DHCP entries for the given IP."""
    ip = str(ip).strip().strip('.')
    for e in DHCPEntry.objects.filter(ip=ip):
        e.delete()


def reset_dhcp(ip, mac):
    mac = MACAddressField.normalize(mac)
    ip = str(ip).strip().strip('.')
    entry = DHCPEntry(ip=ip, mac=mac)
    entry.save()


def generate_dhcp_config():
    template = loader.get_template('dnsedit/dhcp.conf')
    try:
        last = DHCPEntry.objects.order_by('-modified')[0]
        last_modified_date = last.modified.strftime('%Y-%m-%d %H:%M:%S')
    except IndexError:
        last_modified_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    def entries():
        for macaddr, in DHCPEntry.objects.values_list('mac').distinct():
            ips = []
            for ip, in DHCPEntry.objects.filter(mac=macaddr).values_list('ip'):
                ips.append(ip)
            name = ips[0] # XXX Get the correct name from DNS
            address = ', '.join(ips)
            mac = ':'.join('%s%s' % c for c in zip(macaddr[::2],
                                                   macaddr[1::2])).upper()
            yield name, address, mac
    c = Context({
        'entries': entries,
        'last_modified_date': last_modified_date,
    })
    return template.render(c)


def get_domain(name):
    domains = [d for d in Domain.objects.all() if
               name.endswith(d.name)]
    domains.sort(key=lambda d: -len(d.name))
    if domains:
        return domains[0]


def get_revdns_records(ip):
    revname = '.'.join(reversed(ip.split('.'))) + '.in-addr.arpa'
    return Record.objects.filter(name=revname, type='PTR')


def set_revdns_record(ip, name, ttl=None, prio=None, overwrite=False):
    revname = '.'.join(reversed(ip.split('.'))) + '.in-addr.arpa'
    domain_name = '.'.join(list(reversed(ip.split('.')))[1:]) + '.in-addr.arpa'
    domain, created = Domain.objects.get_or_create(name=domain_name)
    records = Record.objects.filter(name=revname, type='PTR')
    if records:
        if not overwrite:
            raise ValueError('RevDNS record already exists')
    else:
        records = [Record(name=revname, type='PTR')]
    for record in records:
        record.content = name
        record.domain = domain
        if ttl is not None:
            record.ttl = ttl
        if prio is not None:
            record.prio = prio
        record.save()
