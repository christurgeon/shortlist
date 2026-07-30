from datetime import date
from pathlib import Path

from shortlist.scout.insider import InsiderTxn, parse_form4_xml

FIX = Path(__file__).parent / "fixtures" / "form4"


def _oklo() -> str:
    return (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace")


def test_parses_a_real_open_market_purchase():
    txns = parse_form4_xml(_oklo())
    buys = [t for t in txns if t.code == "P"]
    assert len(buys) == 1
    t = buys[0]
    assert isinstance(t, InsiderTxn)
    assert t.owner_cik == "0002021774"
    assert t.ticker == "OKLO"
    assert t.date == date(2025, 3, 27)
    assert t.shares == 6000.0
    assert t.price == 24.5686  # verbatim <transactionPricePerShare><value>; DERA rounds this to 24.57
    assert t.plan_10b5_1 is False          # aff10b5One is "0" here, NOT "false"
    assert "director" in t.roles
    assert t.joint_filing is False         # single <reportingOwner> block


def test_missing_price_is_none_not_a_crash():
    """transactionPricePerShare can hold only a <footnoteId> (e.g. option exercises)."""
    xml = """<ownershipDocument>
      <issuer><issuerCik>1</issuerCik><issuerTradingSymbol>ZZZ</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik></reportingOwnerId>
        <reportingOwnerRelationship><isOfficer>true</isOfficer></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionDate><value>2025-01-02</value></transactionDate>
        <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>10</value></transactionShares>
          <transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare>
        </transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    t = parse_form4_xml(xml)[0]
    assert t.price is None and t.shares == 10.0


def test_aff10b5one_accepts_both_encodings():
    def _mk(flag):
        return f"""<ownershipDocument>
          <issuer><issuerCik>1</issuerCik><issuerTradingSymbol>ZZZ</issuerTradingSymbol></issuer>
          <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik></reportingOwnerId>
            <reportingOwnerRelationship><isDirector>true</isDirector></reportingOwnerRelationship>
          </reportingOwner>
          <aff10b5One>{flag}</aff10b5One>
          <nonDerivativeTable><nonDerivativeTransaction>
            <transactionDate><value>2025-01-02</value></transactionDate>
            <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
            <transactionAmounts>
              <transactionShares><value>10</value></transactionShares>
              <transactionPricePerShare><value>5</value></transactionPricePerShare>
            </transactionAmounts>
          </nonDerivativeTransaction></nonDerivativeTable>
        </ownershipDocument>"""
    assert parse_form4_xml(_mk("1"))[0].plan_10b5_1 is True
    assert parse_form4_xml(_mk("true"))[0].plan_10b5_1 is True
    assert parse_form4_xml(_mk("0"))[0].plan_10b5_1 is False
    assert parse_form4_xml(_mk("false"))[0].plan_10b5_1 is False


def test_absent_relationship_flag_is_false_not_missing():
    """Apple's real filing has <isOfficer> and NO <isDirector> element at all."""
    xml = """<ownershipDocument>
      <issuer><issuerCik>1</issuerCik><issuerTradingSymbol>ZZZ</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik></reportingOwnerId>
        <reportingOwnerRelationship><isOfficer>true</isOfficer>
          <officerTitle>CFO</officerTitle></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionDate><value>2025-01-02</value></transactionDate>
        <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
        <transactionAmounts><transactionShares><value>1</value></transactionShares>
        <transactionPricePerShare><value>1</value></transactionPricePerShare></transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    t = parse_form4_xml(xml)[0]
    assert t.roles == frozenset({"officer"})
    assert t.title == "CFO"


def test_malformed_xml_abstains_rather_than_raising():
    assert parse_form4_xml("<not-xml") == []
    assert parse_form4_xml("") == []


def test_joint_filing_flag_set_from_reporting_owner_count():
    """A joint filing (several <reportingOwner> blocks on one Form 4) has no
    per-owner attribution -- the transaction is reported once but owner_cik is a
    guess. The parser must flag it (joint_filing=True), never silently pick one."""
    joint_xml = """<ownershipDocument>
      <issuer><issuerCik>1</issuerCik><issuerTradingSymbol>ZZZ</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik></reportingOwnerId>
        <reportingOwnerRelationship><isDirector>true</isDirector></reportingOwnerRelationship>
      </reportingOwner>
      <reportingOwner><reportingOwnerId><rptOwnerCik>10</rptOwnerCik></reportingOwnerId>
        <reportingOwnerRelationship><isTenPercentOwner>true</isTenPercentOwner></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionDate><value>2025-01-02</value></transactionDate>
        <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>10</value></transactionShares>
          <transactionPricePerShare><value>5</value></transactionPricePerShare>
        </transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    single_xml = """<ownershipDocument>
      <issuer><issuerCik>1</issuerCik><issuerTradingSymbol>ZZZ</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik></reportingOwnerId>
        <reportingOwnerRelationship><isDirector>true</isDirector></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionDate><value>2025-01-02</value></transactionDate>
        <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>10</value></transactionShares>
          <transactionPricePerShare><value>5</value></transactionPricePerShare>
        </transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    assert parse_form4_xml(joint_xml)[0].joint_filing is True
    assert parse_form4_xml(single_xml)[0].joint_filing is False


def test_aff10b5one_wrapped_in_a_value_element_is_read():
    """The parser reads every other scalar through a <value> child. Reading aff10b5One via
    raw .text meant a filer agent emitting the nested form would silently yield False --
    disabling the 10b5-1 exclusion entirely, with nothing to notice it."""
    xml = """<ownershipDocument>
      <issuer><issuerCik>1</issuerCik><issuerTradingSymbol>ZZZ</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>9</rptOwnerCik></reportingOwnerId>
        <reportingOwnerRelationship><isDirector>true</isDirector></reportingOwnerRelationship>
      </reportingOwner>
      <aff10b5One><value>1</value></aff10b5One>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionDate><value>2025-01-02</value></transactionDate>
        <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>10</value></transactionShares>
          <transactionPricePerShare><value>5</value></transactionPricePerShare>
        </transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    assert parse_form4_xml(xml)[0].plan_10b5_1 is True
