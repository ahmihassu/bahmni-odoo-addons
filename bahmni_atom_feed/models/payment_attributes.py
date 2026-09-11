# -*- coding: utf-8 -*-
"""OpenMRS person attributes related to Ethiopian payment classification."""

# Whitelist names must match OpenMRS person attribute type names exactly.
PAYMENT_PERSON_ATTRIBUTES = (
    'PaymentMethod',
    'Credit Information',
    'Credit Companies',
    'Free',
    'InsuranceID',
    'InsuranceName',
    'InsuranceCode',
    'InsuranceZone',
    'InsuranceExpiryDate',
    'CBHI ID',
    'CBHIExpiryDate',
    'CBHI Region',
    'CBHI Zone',
    'CBHI Woreda',
)

# OpenMRS attribute name → res.partner field (synced, cashier-readonly).
PAYMENT_ATTR_FIELD_MAP = {
    'PaymentMethod': 'payment_method',
    'Credit Information': 'credit_information',
    'Credit Companies': 'credit_companies',
    'Free': 'free_reason',
    'InsuranceID': 'insurance_id',
    'InsuranceName': 'insurance_name',
    'InsuranceCode': 'insurance_code',
    'InsuranceZone': 'insurance_zone',
    'InsuranceExpiryDate': 'insurance_expiry_date',
    'CBHI ID': 'cbhi_id',
    'CBHIExpiryDate': 'cbhi_expiry_date',
    'CBHI Region': 'cbhi_region',
    'CBHI Zone': 'cbhi_zone',
    'CBHI Woreda': 'cbhi_woreda',
}
