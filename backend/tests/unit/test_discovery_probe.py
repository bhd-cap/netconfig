"""
Platform identification and the credential-trying probe, without a network.

Identification is the fix for "every discovered device is Cisco IOS": the old
code fell back to the seed's own type, so a crawl from one Cisco switch
labelled the whole estate cisco_ios. The important behaviour here is that an
unrecognised platform returns None rather than a guess.
"""
from unittest import mock

import pytest

from app.services import discovery_probe as probe
from app.services.credentials import CLI, SNMP, CredentialAttempt


def cli_cred(name, username="admin", password="secret", credential_id=1):
    return CredentialAttempt(
        id=credential_id, name=name, kind=CLI, username=username, password=password
    )


def snmp_cred(name, community="public", version="2c", credential_id=1):
    return CredentialAttempt(
        id=credential_id,
        name=name,
        kind=SNMP,
        snmp_version=version,
        community=community,
    )


# --------------------------------------------------------------------------
# Identification
# --------------------------------------------------------------------------

REAL_SYSDESCRS = [
    (
        "Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version "
        "15.0(2)SE11, RELEASE SOFTWARE (fc2)",
        "cisco_ios",
    ),
    ("Cisco IOS XE Software, Version 16.09.04", "cisco_ios_xe"),
    (
        "Cisco Nexus Operating System (NX-OS) Software 9.3(5), TAC support: "
        "http://www.cisco.com/tac",
        "cisco_nxos",
    ),
    (
        "Arista Networks EOS version 4.24.2F running on an Arista Networks "
        "DCS-7050SX-64",
        "arista_eos",
    ),
    ("FortiGate-60F v7.0.5,build0304,220429 (GA)", "fortinet"),
    (
        "Juniper Networks, Inc. ex4300-48t Ethernet Switch, kernel JUNOS "
        "18.4R3-S9",
        "juniper_junos",
    ),
    ("ArubaOS (MODEL: 7010), Version 8.6.0.4", "aruba_os"),
    ("HP J9146A ProCurve Switch 2910al-24G, revision W.15.16.0007", "hp_procurve"),
    ("HPE Comware Software, Version 7.1.070, Release 6318P01", "hp_comware"),
]


@pytest.mark.parametrize("sysdescr,expected", REAL_SYSDESCRS)
def test_identifies_each_platform(sysdescr, expected):
    assert probe.identify_platform(sysdescr) == expected


def test_an_unrecognised_platform_returns_none():
    """
    None is the whole point

    Falling back to the seed's device type is what made a crawl from a Cisco
    switch register every neighbour as cisco_ios - including the Juniper and
    the firewall - and then drive them all with a Cisco driver.
    """
    assert probe.identify_platform("Some Unknown Vendor Router OS 1.0") is None
    assert probe.identify_platform("") is None
    assert probe.identify_platform(None) is None
    assert probe.identify_platform(None, "", "   ") is None


def test_more_specific_platforms_win():
    """IOS-XE and NX-OS both contain 'Cisco' and must not collapse to cisco_ios"""
    assert probe.identify_platform("Cisco IOS XE Software") == "cisco_ios_xe"
    assert probe.identify_platform("Cisco NX-OS") == "cisco_nxos"
    assert probe.identify_platform("Cisco IOS Software") == "cisco_ios"


def test_sources_are_tried_in_order():
    """The first source that identifies anything wins"""
    assert (
        probe.identify_platform(None, "Arista Networks EOS", "Cisco IOS")
        == "arista_eos"
    )
    # An unhelpful first source falls through to the next.
    assert probe.identify_platform("unknown box", "Cisco IOS") == "cisco_ios"


def test_only_catalogue_types_are_returned():
    """A pattern must never yield a device type the connector cannot drive"""
    from app.config.device_types import DEVICE_TYPE_CONFIG

    for sysdescr, _ in REAL_SYSDESCRS:
        identified = probe.identify_platform(sysdescr)
        assert identified in DEVICE_TYPE_CONFIG


@pytest.mark.parametrize(
    "text,model,version",
    [
        (
            "Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.0(2)SE11",
            "C2960",
            "15.0(2)SE11",
        ),
        (
            "Arista Networks EOS version 4.24.2F running on an Arista Networks DCS-7050SX",
            "DCS-7050SX",
            "4.24.2F",
        ),
        ("FortiGate-60F v7.0.5,build0304", "FortiGate-60F", "7.0.5"),
    ],
)
def test_parses_model_and_version(text, model, version):
    facts = probe.parse_facts(text)
    assert facts.get("model") == model
    assert facts.get("os_version") == version


def test_parse_facts_survives_nothing_useful():
    assert probe.parse_facts(None) == {}
    assert probe.parse_facts("") == {}
    assert probe.parse_facts("a router") == {}


# --------------------------------------------------------------------------
# CLI probing
# --------------------------------------------------------------------------


def test_a_closed_port_is_not_worth_a_login_attempt():
    """
    Six credentials against a closed port is six timeouts to learn nothing

    One TCP connect already answered the question.
    """
    with mock.patch.object(probe, "port_open", return_value=False) as port, mock.patch.object(
        probe, "_login"
    ) as login:
        outcomes = probe.probe_cli("10.0.0.1", [cli_cred("first"), cli_cred("second")])

    login.assert_not_called()
    assert port.call_count == 2  # ssh, then telnet
    assert [o.transport for o in outcomes] == ["ssh", "telnet"]
    assert all(o.result == probe.UNREACHABLE for o in outcomes)


def test_each_credential_is_tried_until_one_is_accepted():
    from netmiko.exceptions import NetmikoAuthenticationException

    connection = mock.Mock()
    connection.send_command.return_value = "Cisco IOS Software, Version 15.0"

    def login(host, port, transport, attempt, device_type, timeout=20):
        if attempt.name != "third":
            raise NetmikoAuthenticationException("Authentication failed")
        return connection

    creds = [
        cli_cred("first", credential_id=1),
        cli_cred("second", credential_id=2),
        cli_cred("third", credential_id=3),
    ]

    with mock.patch.object(probe, "port_open", return_value=True), mock.patch.object(
        probe, "_login", side_effect=login
    ):
        outcomes = probe.probe_cli("10.0.0.1", creds)

    ssh = outcomes[0]
    assert ssh.transport == "ssh"
    assert ssh.result == probe.SUCCESS
    assert ssh.credential_name == "third"
    assert ssh.attempts == 3
    # Telnet is never tried once SSH works.
    assert len(outcomes) == 1


def test_telnet_is_tried_when_ssh_rejects_every_credential():
    from netmiko.exceptions import NetmikoAuthenticationException

    connection = mock.Mock()
    connection.send_command.return_value = "HP J9146A ProCurve Switch 2910al-24G"

    def login(host, port, transport, attempt, device_type, timeout=20):
        if transport == "ssh":
            raise NetmikoAuthenticationException("no")
        return connection

    with mock.patch.object(probe, "port_open", return_value=True), mock.patch.object(
        probe, "_login", side_effect=login
    ):
        outcomes = probe.probe_cli("10.0.0.1", [cli_cred("only")])

    assert [o.transport for o in outcomes] == ["ssh", "telnet"]
    assert outcomes[0].result == probe.AUTH_FAILED
    assert outcomes[1].result == probe.SUCCESS
    assert outcomes[1].facts["device_type"] == "hp_procurve"


def test_a_session_error_stops_that_transport_rather_than_retrying():
    """
    A driver mismatch or a timeout is not a password problem

    Trying five more credentials against it just repeats the same timeout.
    """
    creds = [cli_cred(f"c{n}", credential_id=n) for n in range(1, 6)]
    calls = []

    def login(host, port, transport, attempt, device_type, timeout=20):
        calls.append((transport, attempt.name))
        raise TimeoutError("timed out waiting for a prompt")

    with mock.patch.object(probe, "port_open", return_value=True), mock.patch.object(
        probe, "_login", side_effect=login
    ):
        outcomes = probe.probe_cli("10.0.0.1", creds)

    # One attempt per transport, not five.
    assert [name for _, name in calls] == ["c1", "c1"]
    assert all(o.result == probe.AUTH_FAILED for o in outcomes)
    assert "timed out" in outcomes[0].message


def test_an_open_port_with_no_credentials_says_so():
    with mock.patch.object(probe, "port_open", return_value=True):
        outcomes = probe.probe_cli("10.0.0.1", [])

    assert outcomes[0].result == probe.AUTH_FAILED
    assert "no CLI credentials are configured" in outcomes[0].message


def test_the_probe_uses_a_neutral_driver_for_an_unknown_device():
    """
    Guessing cisco_ios makes Netmiko wait for a prompt a Juniper never prints

    The probe has to open the session without assuming a platform.
    """
    assert probe._netmiko_driver(None, "ssh") == "terminal_server"
    assert probe._netmiko_driver(None, "telnet") == "generic_telnet"
    assert probe._netmiko_driver("not_a_real_type", "ssh") == "terminal_server"
    # A known type uses its real driver.
    assert probe._netmiko_driver("cisco_ios", "ssh") == "cisco_ios"


# --------------------------------------------------------------------------
# SNMP probing
# --------------------------------------------------------------------------


def test_snmp_tries_each_community_until_one_answers():
    from app.services.snmp_client import SnmpError

    answered = {
        "sysName": "core-01",
        "sysDescr": "Cisco IOS Software, Version 15.0(2)SE11",
        "sysLocation": "Comms room",
        "sysContact": "noc@example.com",
    }

    class FakeClient:
        def __init__(self, **kwargs):
            self.community = kwargs.get("community")

        def system_info(self):
            if self.community != "correct":
                raise SnmpError("No SNMP response received before timeout")
            return answered

    creds = [
        snmp_cred("public", community="public", credential_id=1),
        snmp_cred("private", community="private", credential_id=2),
        snmp_cred("real", community="correct", credential_id=3),
    ]

    with mock.patch("app.services.snmp_client.SnmpClient", FakeClient):
        outcome = probe.probe_snmp("10.0.0.1", creds)

    assert outcome.result == probe.SUCCESS
    assert outcome.credential_name == "real"
    assert outcome.attempts == 3
    assert outcome.facts["snmp_sysname"] == "core-01"
    assert outcome.facts["device_type"] == "cisco_ios"
    assert outcome.facts["os_version"] == "15.0(2)SE11"


def test_snmp_with_no_credentials_reports_that():
    outcome = probe.probe_snmp("10.0.0.1", [])

    assert outcome.result == probe.UNREACHABLE
    assert "No SNMP credentials" in outcome.message


def test_snmp_reports_when_every_community_is_refused():
    from app.services.snmp_client import SnmpError

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def system_info(self):
            raise SnmpError("timeout")

    with mock.patch("app.services.snmp_client.SnmpClient", FakeClient):
        outcome = probe.probe_snmp("10.0.0.1", [snmp_cred("a"), snmp_cred("b")])

    assert outcome.result == probe.AUTH_FAILED
    assert outcome.attempts == 2


# --------------------------------------------------------------------------
# The full assessment
# --------------------------------------------------------------------------


def test_a_device_that_authenticates_is_backup_eligible():
    connection = mock.Mock()
    connection.send_command.return_value = (
        "Arista Networks EOS version 4.24.2F running on an Arista DCS-7050SX"
    )

    with mock.patch.object(probe, "port_open", return_value=True), mock.patch.object(
        probe, "_login", return_value=connection
    ):
        assessment = probe.assess("10.0.0.1", [cli_cred("works")])

    assert assessment.backup_eligible is True
    assert assessment.auth_status == probe.SUCCESS
    assert assessment.transport == "ssh"
    assert assessment.device_type == "arista_eos"
    assert assessment.credential_id == 1


def test_a_device_that_rejects_everything_is_not_eligible_but_is_assessed():
    """
    The device still belongs in the inventory, just not on a schedule
    """
    from netmiko.exceptions import NetmikoAuthenticationException

    with mock.patch.object(probe, "port_open", return_value=True), mock.patch.object(
        probe, "_login", side_effect=NetmikoAuthenticationException("denied")
    ):
        assessment = probe.assess("10.0.0.1", [cli_cred("wrong")])

    assert assessment.backup_eligible is False
    assert assessment.auth_status == probe.AUTH_FAILED
    assert "ssh" in assessment.auth_error
    assert "telnet" in assessment.auth_error


def test_an_unreachable_device_is_distinguished_from_a_refused_login():
    """
    Nothing listening is a routing problem; a rejected login is a password
    problem. Reporting both as "inactive" leaves an operator guessing.
    """
    with mock.patch.object(probe, "port_open", return_value=False):
        assessment = probe.assess("10.0.0.1", [cli_cred("any")])

    assert assessment.auth_status == probe.UNREACHABLE
    assert assessment.backup_eligible is False


def test_an_snmp_only_device_is_identified_but_not_eligible():
    """
    SNMP cannot retrieve a configuration

    So an SNMP-only device is inventoried, identified and crawlable, but never
    scheduled for backup.
    """
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def system_info(self):
            return {
                "sysName": "printer-01",
                "sysDescr": "HP J9146A ProCurve Switch 2910al-24G",
                "sysLocation": None,
                "sysContact": None,
            }

    with mock.patch("app.services.snmp_client.SnmpClient", FakeClient), mock.patch.object(
        probe, "port_open", return_value=False
    ):
        assessment = probe.assess(
            "10.0.0.1", cli_attempts=[cli_cred("any")], snmp_attempts=[snmp_cred("public")]
        )

    assert assessment.backup_eligible is False
    assert assessment.transport == "snmp"
    assert assessment.device_type == "hp_procurve"
    assert assessment.facts["snmp_sysname"] == "printer-01"


def test_cli_identification_overrides_the_snmp_guess():
    """
    A version command from an authenticated session is the best evidence
    """
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def system_info(self):
            return {"sysName": "sw", "sysDescr": "Cisco IOS Software", "sysLocation": None,
                    "sysContact": None}

    connection = mock.Mock()
    connection.send_command.return_value = "Cisco IOS XE Software, Version 16.9.4"

    with mock.patch("app.services.snmp_client.SnmpClient", FakeClient), mock.patch.object(
        probe, "port_open", return_value=True
    ), mock.patch.object(probe, "_login", return_value=connection):
        assessment = probe.assess(
            "10.0.0.1", [cli_cred("works")], [snmp_cred("public")]
        )

    assert assessment.device_type == "cisco_ios_xe"


def test_a_neighbour_hint_is_used_when_nothing_else_identifies():
    from netmiko.exceptions import NetmikoAuthenticationException

    with mock.patch.object(probe, "port_open", return_value=True), mock.patch.object(
        probe, "_login", side_effect=NetmikoAuthenticationException("no")
    ):
        assessment = probe.assess(
            "10.0.0.1",
            [cli_cred("wrong")],
            platform_hint="Juniper Networks ex4300-48t",
        )

    assert assessment.device_type == "juniper_junos"
    assert assessment.backup_eligible is False
