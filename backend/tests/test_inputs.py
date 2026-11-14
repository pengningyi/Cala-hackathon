from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import RoundInput


def test_conditional_interest_and_conditional_lead_ticket_do_not_reduce_gap():
    round = RoundInput(stage="seed", target_eur="2000000", firm_commitments_eur="1000000",
                       conditional_interest_eur="800000", lead_status="conditional", lead_ticket_eur="1500000")
    assert round.remaining_eur == Decimal("1000000")


@pytest.mark.parametrize("changes", [
    {"target_eur": "0"}, {"firm_commitments_eur": "-1"}, {"conditional_interest_eur": "-1"},
    {"lead_ticket_eur": "101"}, {"target_eur": "NaN"}, {"target_eur": "100.001"},
])
def test_invalid_money_and_overstated_firm_lead_ticket_are_rejected(changes):
    data = {"stage": "series_a", "target_eur": "1000", "firm_commitments_eur": "100",
            "lead_status": "signed", "lead_ticket_eur": "100"}
    with pytest.raises(ValidationError):
        RoundInput.model_validate({**data, **changes})
