// Negative control for ci/bicep_check.py: this file compiles, but the linter
// warns about the unused parameter below, so the check must FAIL on it.
// Deliberately outside infra/, so infra/bicepconfig.json does not apply and the
// diagnostic stays a warning, which is the case the check exists to catch.

param unusedOnPurpose string = 'agon'

output fixture string = 'negative control'
