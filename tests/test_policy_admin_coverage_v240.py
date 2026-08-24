from copy import deepcopy
from pathlib import Path

from landvalue360_portal.database import session_scope
from landvalue360_portal.financial_engine import (
    default_portal_policy_controls,
    normalize_financial_model,
)
from landvalue360_portal.services import create_staff_user


LOCKED_FINANCIAL_INTEGRITY_CONTROLS = {
    "allow_negative_cash",
    "require_terminal_debt_zero",
    "require_deferred_cost_zero",
    "require_contractual_arrears_zero",
    "require_monthly_cash_reconciliation",
}


def _login(client, email: str) -> str:
    response = client.post('/api/auth/login', json={
        'email': email, 'password': 'StrongPass123!',
    })
    assert response.status_code == 200, response.text
    return response.json()['csrf_token']


def test_admin_form_covers_every_editable_policy_assumption():
    """Every policy assumption has an administrator control.

    Accounting integrity requirements remain deliberately locked. They are
    engine invariants agreed for the portal, not discretionary assumptions.
    """
    defaults = default_portal_policy_controls()
    html = Path('app/landvalue360_portal/templates/admin.html').read_text(encoding='utf-8')
    javascript = Path('app/landvalue360_portal/static/admin.js').read_text(encoding='utf-8')

    excluded_top_level = {'schema_version', 'default_timing'}
    for key in defaults:
        if key in excluded_top_level or key in {'advanced_defaults', 'finance_policy'}:
            continue
        assert f'name="{key}"' in html, key

    assert "querySelectorAll('[data-policy-rate]')" in javascript
    assert 'readFinancialPolicyControls' in javascript
    assert 'populateFinancialPolicyForm' in javascript

    for key in defaults['advanced_defaults']:
        field = f'advanced_{key}'
        assert f'name="{field}"' in html, field

    for key in defaults['finance_policy']:
        if key in LOCKED_FINANCIAL_INTEGRITY_CONTROLS:
            continue
        field = f'finance_{key}'
        assert f'name="{field}"' in html, field

    finance = defaults['finance_policy']
    assert finance['allow_negative_cash'] is False
    assert finance['require_terminal_debt_zero'] is True
    assert finance['require_deferred_cost_zero'] is True
    assert finance['require_contractual_arrears_zero'] is True
    assert finance['require_monthly_cash_reconciliation'] is True


def test_admin_can_publish_and_reload_full_assumption_set(client):
    with session_scope() as db:
        create_staff_user(
            db, email='policy-coverage-admin@example.com', password='StrongPass123!',
            full_name='Policy Coverage Admin', role_code='PLATFORM_ADMIN',
        )
    csrf = _login(client, 'policy-coverage-admin@example.com')
    current_response = client.get('/api/admin/financial-policy')
    assert current_response.status_code == 200, current_response.text
    current = current_response.json()['current']

    controls = deepcopy(current['controls'])
    controls.update({
        'display_name_ar': 'سياسة اختبار شاملة',
        'display_name_en': 'Comprehensive Test Policy',
        'description_ar': 'نسخة تحقق من دوام جميع الافتراضات القابلة للتعديل.',
        'description_en': 'Persistence test for every editable policy assumption.',
        'user_selectable': True,
        'discount_rate': '0.135',
        'government_discount_rate': '0.095',
        'minimum_project_npv': '125000',
        'minimum_developer_equity_irr': '0.17',
        'target_developer_irr': '0.23',
        'minimum_developer_npv': '250000',
        'minimum_profit_on_cost': '0.18',
        'target_developer_profit_on_cost': '0.27',
        'minimum_developer_multiple': '1.35',
        'maximum_funding_gap': '50000',
        'minimum_landowner_npv': '1000000',
        'minimum_landowner_value_recovery': '0.90',
        'minimum_landowner_share': '0.02',
        'maximum_landowner_share': '0.42',
        'search_tolerance': '0.00002',
        'negotiation_recommendation_method': 'POLICY_RANGE_POSITION',
        'institutional_conservatism': '0.35',
        'risk_adjusted_capacity_factor': '0.40',
        'minimum_capacity_factor': '0.25',
        'developer_safety_buffer': '0.03',
        'balanced_position_factor': '0.55',
        'balanced_position_minimum': '0.15',
        'balanced_position_maximum': '0.80',
        'developer_competitive_position_factor': '0.38',
        'rounding_increment_percent': '0.0025',
        'allowed_contract_methods': ['GROSS_SALES', 'NET_SALES', 'UPFRONT', 'HYBRID'],
        'net_sales_deductible_categories': [],
        'profit_share_cost_categories': ['CONSTRUCTION', 'INFRASTRUCTURE', 'MANAGEMENT'],
        'proposal_selection_method': 'BALANCED',
    })
    controls['finance_policy'] = {
        **controls['finance_policy'],
        'allow_financing': False,
        'defer_unfunded_costs': False,
        # Integrity invariants are intentionally kept mandatory.
        'allow_negative_cash': False,
        'require_terminal_debt_zero': True,
        'require_deferred_cost_zero': True,
        'require_contractual_arrears_zero': True,
        'require_monthly_cash_reconciliation': True,
    }
    controls['advanced_defaults'] = {
        **controls['advanced_defaults'],
        'finance_enabled': False,
        'committed_financing': '0',
        'annual_interest_rate': '0.085',
        'upfront_fee_rate': '0.012',
        'commitment_fee_rate': '0.004',
        'cash_sweep_share': '0.85',
        'capitalize_interest': False,
        'force_terminal_repayment': True,
        'minimum_cash_balance': '250000',
        'funding_draw_order': 'EQUITY_FIRST',
        'spend_policy': 'CASH_DRIVEN',
        'hybrid_minimum_execution_share': '0.42',
        'future_cost_reserve_share': '0.08',
        'defer_contractual_payments': False,
        'sales_curve_type': 'BACK_LOADED',
        'sales_curve_intensity': '1.25',
        'construction_curve_type': 'S_CURVE',
        'other_cost_curve_type': 'LINEAR',
        'maximum_extension_months': 84,
        'maximum_monthly_execution_share': '0.12',
        'maximum_monthly_execution_amount': '7500000',
        'commercial_discount_rate': '0.04',
        'buyer_incentive_rate': '0.015',
        'refund_rate': '0.01',
        'cost_escalation_rate': '0.03',
        'cost_contingency_rate': '0.06',
        'horizon_buffer_months': 18,
        'solver_grid_intervals': 20,
        'distribution_frequency_code': 'QUARTERLY',
        'first_distribution_month': 18,
        'distribution_share': '0.75',
        'distribution_reserve_months': 9,
        'remaining_cost_reserve_share': '0.30',
        'prohibit_distributions_while_debt_outstanding': True,
        'recover_developer_advances_before_landowner_cash': False,
        'settle_prior_obligations_before_distribution': True,
        'prohibit_before_completion': True,
        'upfront_search_land_value_multiple': '5',
        'upfront_search_cost_multiple': '2.5',
        'collection_rules': [
            {'lag_months': 0, 'weight': '0.25', 'label': 'Booking'},
            {'lag_months': 10, 'weight': '0.35', 'label': 'Construction'},
            {'lag_months': 22, 'weight': '0.40', 'label': 'Handover'},
        ],
    }
    controls['default_timing'] = {
        'sales_curve_type': 'BACK_LOADED',
        'cost_curve_type': 'S_CURVE',
        'funding_draw_order': 'EQUITY_FIRST',
        'spend_policy': 'CASH_DRIVEN',
        'defer_contractual_payments': False,
        'collection_rules': controls['advanced_defaults']['collection_rules'],
    }

    created = client.post('/api/admin/financial-policy/versions', json={
        'controls': controls,
        'change_reason': 'Full policy assumption persistence test',
        'source_version_id': current['id'],
        'activate': False,
    }, headers={'X-CSRF-Token': csrf})
    assert created.status_code == 201, created.text
    version_id = created.json()['id']

    loaded = client.get(f'/api/admin/financial-policy/versions/{version_id}')
    assert loaded.status_code == 200, loaded.text
    persisted = loaded.json()['controls']
    assert persisted['display_name_en'] == 'Comprehensive Test Policy'
    assert persisted['discount_rate'] == '0.135'
    assert persisted['minimum_landowner_value_recovery'] == '0.90'
    assert persisted['balanced_position_factor'] == '0.55'
    assert persisted['allowed_contract_methods'] == ['GROSS_SALES', 'NET_SALES', 'UPFRONT', 'HYBRID']
    assert persisted['finance_policy']['allow_financing'] is False
    assert persisted['finance_policy']['allow_negative_cash'] is False
    assert persisted['advanced_defaults']['sales_curve_type'] == 'BACK_LOADED'
    assert persisted['advanced_defaults']['construction_curve_type'] == 'S_CURVE'
    assert persisted['advanced_defaults']['distribution_frequency_code'] == 'QUARTERLY'
    assert persisted['advanced_defaults']['solver_grid_intervals'] == 20
    assert persisted['advanced_defaults']['collection_rules'][2]['weight'] == '0.4'

    normalized = normalize_financial_model({}, controls=persisted)
    assert normalized['advanced_overrides_enabled'] is False
    assert normalized['sales']['curve_type'] == 'BACK_LOADED'
    assert normalized['sales']['commercial_discount_rate'] == '0.04'
    assert normalized['delivery']['construction_curve_type'] == 'S_CURVE'
    assert normalized['delivery']['other_cost_curve_type'] == 'LINEAR'
    assert normalized['delivery']['cost_contingency_rate'] == '0.06'
    assert normalized['finance']['enabled'] is False
    assert normalized['finance']['minimum_cash_balance'] == '250000'
    assert normalized['sales']['collection_rules'][1]['lag_months'] == 10

    # The source version remains immutable and unchanged.
    source_after = client.get(f"/api/admin/financial-policy/versions/{current['id']}").json()
    assert source_after['snapshot_hash'] == current['snapshot_hash']
    assert source_after['controls']['discount_rate'] != '0.135'
