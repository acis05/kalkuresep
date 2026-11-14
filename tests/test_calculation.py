from decimal import Decimal
def test_scaling():
    assert Decimal('1000')*Decimal('50')/Decimal('20')==Decimal('2500')
def test_bakers_percentage():
    assert Decimal('600')/Decimal('1000')*100==60
