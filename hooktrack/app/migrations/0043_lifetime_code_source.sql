-- Where a licence came from: 'api' = AppSumo told us by webhook, 'code' = one of the codes we generated and gave AppSumo as a CSV.
-- A code only counts as sold once someone redeems it (claimed by a workspace).
ALTER TABLE lifetime_licenses ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'api';
