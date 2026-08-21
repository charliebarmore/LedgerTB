import sqlite3

from constants import AccountSubtype


# Entity type definitions (legal structure)
ENTITY_TYPES = {
    "S-Corporation": "Corporation with pass-through taxation. Requires shareholder distributions, officer compensation tracking.",
    "C-Corporation": "Standard corporation with corporate-level taxation. Includes dividends, corporate tax accounts.",
    "LLC (Single-Member)": "Disregarded entity for tax purposes. Simple owner's capital/draws structure.",
    "LLC (Partnership)": "Multi-member LLC taxed as partnership. Member capital accounts and distributions.",
    "Partnership": "Partnership with multiple partners. Partner capital accounts and guaranteed payments.",
    "Sole Proprietorship": "Unincorporated business owned by one person. Simplest equity structure.",
    "Non-Profit": "Tax-exempt organization. Uses net assets instead of equity, tracks restricted funds.",
}


# Business/Industry type definitions
BUSINESS_TYPES = {
    "Professional Services": "Accounting, legal, consulting, engineering, medical practices. No inventory or COGS.",
    "Retail": "Brick-and-mortar store selling products. Includes inventory and cost of goods sold.",
    "E-commerce": "Online sales of products. Includes inventory, COGS, and shipping accounts.",
    "Wholesale/Distribution": "Buying and reselling products in bulk. Inventory and COGS focused.",
    "Restaurant/Food Service": "Restaurants, cafes, catering. Food inventory and COGS.",
    "Real Estate (Rental)": "Rental property income. Property assets, rental income, property expenses.",
    "Construction/Contractor": "Construction and contracting. Job costing, work in progress.",
    "Manufacturing": "Producing goods. Raw materials, WIP, finished goods inventory.",
    "Personal/Individual": "Personal finances tracking. Simple income and expense categories.",
    "Other": "General business. Basic accounts without industry-specific items.",
}


# =============================================================================
# CORE ACCOUNTS - Everyone gets these
# =============================================================================
CORE_ACCOUNTS = [
    # Assets (1000-1999)
    ("1000", "Cash - Operating", "Asset", "Cash"),
    ("1010", "Cash - Savings", "Asset", "Cash"),
    ("1100", "Accounts Receivable", "Asset", "Receivable"),
    ("1200", "Prepaid Expenses", "Asset", "Prepaid"),
    ("1500", "Furniture & Equipment", "Asset", "Fixed Asset"),
    ("1510", "Accumulated Depreciation - F&E", "Asset", "Contra Asset"),

    # Liabilities (2000-2999)
    ("2000", "Accounts Payable", "Liability", "Payable"),
    ("2100", "Credit Card Payable", "Liability", "Payable"),
    ("2200", "Payroll Liabilities", "Liability", "Payable"),
    ("2300", "Accrued Expenses", "Liability", "Accrual"),

    # Basic Revenue (4000-4999)
    ("4200", "Interest Income", "Revenue", "Other"),
    ("4900", "Other Income", "Revenue", "Other"),

    # Basic Expenses (5000-9999)
    ("6000", "Rent Expense", "Expense", "Occupancy"),
    ("6100", "Utilities", "Expense", "Occupancy"),
    ("6200", "Office Supplies", "Expense", "Operating"),
    ("6300", "Software & Subscriptions", "Expense", "Operating"),
    ("6500", "Insurance", "Expense", "Operating"),
    ("6600", "Professional Fees", "Expense", "Operating"),
    ("6700", "Marketing & Advertising", "Expense", "Operating"),
    ("6800", "Travel", "Expense", "Operating"),
    ("6810", "Meals & Entertainment", "Expense", "Operating"),
    ("6900", "Bank & Merchant Fees", "Expense", "Operating"),
    ("7000", "Depreciation Expense", "Expense", "Non-Cash"),
    ("7500", "Miscellaneous Expense", "Expense", "Other"),
]


# =============================================================================
# ENTITY-SPECIFIC EQUITY ACCOUNTS
# =============================================================================
EQUITY_ACCOUNTS = {
    "S-Corporation": [
        ("3000", "Common Stock", "Equity", "Capital"),
        ("3100", "Additional Paid-In Capital", "Equity", "Capital"),
        ("3200", "Shareholder Distributions", "Equity", "Distributions"),
        ("3300", "Treasury Stock", "Equity", "Capital"),
        ("3900", "Retained Earnings", "Equity", "Retained Earnings"),
    ],
    "C-Corporation": [
        ("3000", "Common Stock", "Equity", "Capital"),
        ("3100", "Additional Paid-In Capital", "Equity", "Capital"),
        ("3200", "Dividends Paid", "Equity", "Dividends"),
        ("3300", "Treasury Stock", "Equity", "Capital"),
        ("3900", "Retained Earnings", "Equity", "Retained Earnings"),
    ],
    "LLC (Single-Member)": [
        ("3000", "Owner's Capital", "Equity", "Capital"),
        ("3100", "Owner's Draws", "Equity", "Draws"),
        ("3900", "Retained Earnings", "Equity", "Retained Earnings"),
    ],
    "LLC (Partnership)": [
        ("3000", "Member Capital - Member 1", "Equity", "Capital"),
        ("3010", "Member Capital - Member 2", "Equity", "Capital"),
        ("3100", "Member Distributions - Member 1", "Equity", "Distributions"),
        ("3110", "Member Distributions - Member 2", "Equity", "Distributions"),
        ("3200", "Guaranteed Payments", "Equity", "Guaranteed Payments"),
        ("3900", "Retained Earnings", "Equity", "Retained Earnings"),
    ],
    "Partnership": [
        ("3000", "Partner Capital - Partner 1", "Equity", "Capital"),
        ("3010", "Partner Capital - Partner 2", "Equity", "Capital"),
        ("3100", "Partner Distributions - Partner 1", "Equity", "Distributions"),
        ("3110", "Partner Distributions - Partner 2", "Equity", "Distributions"),
        ("3200", "Guaranteed Payments", "Equity", "Guaranteed Payments"),
        ("3900", "Retained Earnings", "Equity", "Retained Earnings"),
    ],
    "Sole Proprietorship": [
        ("3000", "Owner's Capital", "Equity", "Capital"),
        ("3100", "Owner's Draws", "Equity", "Draws"),
        ("3900", "Retained Earnings", "Equity", "Retained Earnings"),
    ],
    "Non-Profit": [
        ("3000", "Unrestricted Net Assets", "Equity", "Net Assets"),
        ("3100", "Temporarily Restricted Net Assets", "Equity", "Net Assets"),
        ("3200", "Permanently Restricted Net Assets", "Equity", "Net Assets"),
    ],
}


# Entity-specific non-equity accounts
ENTITY_SPECIFIC_ACCOUNTS = {
    "S-Corporation": [
        ("2500", "Shareholder Loan Payable", "Liability", "Loan"),
        ("5010", "Officer Compensation", "Expense", "Payroll"),
        ("5020", "Officer Health Insurance", "Expense", "Payroll"),
    ],
    "C-Corporation": [
        ("2500", "Income Tax Payable", "Liability", "Tax"),
        ("2510", "Dividends Payable", "Liability", "Payable"),
        ("7200", "Income Tax Expense", "Expense", "Tax"),
    ],
    "LLC (Single-Member)": [
        ("5400", "Self-Employment Tax (Memo)", "Expense", "Tax"),
    ],
    "LLC (Partnership)": [
        ("5010", "Guaranteed Payments Expense", "Expense", "Payroll"),
    ],
    "Partnership": [
        ("5010", "Guaranteed Payments Expense", "Expense", "Payroll"),
    ],
    "Sole Proprietorship": [
        ("5400", "Self-Employment Tax (Memo)", "Expense", "Tax"),
    ],
    "Non-Profit": [
        ("4300", "Contributions & Donations", "Revenue", "Contributions"),
        ("4400", "Grant Revenue", "Revenue", "Grants"),
        ("4500", "Program Service Revenue", "Revenue", "Program"),
        ("4600", "Fundraising Revenue", "Revenue", "Fundraising"),
        ("5500", "Program Expenses", "Expense", "Program"),
        ("5600", "Fundraising Expenses", "Expense", "Fundraising"),
        ("5700", "Management & General", "Expense", "Administrative"),
    ],
}


# =============================================================================
# BUSINESS/INDUSTRY-SPECIFIC ACCOUNTS
# =============================================================================

BUSINESS_TYPE_ACCOUNTS = {
    "Professional Services": [
        # Revenue
        ("4000", "Professional Fees", "Revenue", "Service Revenue"),
        ("4010", "Consulting Revenue", "Revenue", "Service Revenue"),
        ("4020", "Retainer Fees", "Revenue", "Service Revenue"),
        # Assets
        ("1110", "Work in Progress - Unbilled", "Asset", "WIP"),
        ("1120", "Client Trust Account", "Asset", "Trust"),
        # Liabilities
        ("2600", "Client Trust Liability", "Liability", "Trust"),
        ("2610", "Deferred Revenue", "Liability", "Deferred"),
        # Expenses
        ("5000", "Salaries & Wages", "Expense", "Payroll"),
        ("5100", "Payroll Taxes", "Expense", "Payroll"),
        ("5200", "Employee Benefits", "Expense", "Payroll"),
        ("5300", "Contract Labor", "Expense", "Payroll"),
        ("6400", "Professional Development & CPE", "Expense", "Operating"),
        ("6410", "Professional Licenses & Dues", "Expense", "Operating"),
        ("6420", "Reference Materials & Research", "Expense", "Operating"),
        ("6430", "E&O Insurance", "Expense", "Operating"),
    ],

    "Retail": [
        # Assets - Inventory
        ("1300", "Inventory", "Asset", "Inventory"),
        ("1310", "Inventory - In Transit", "Asset", "Inventory"),
        # Liabilities
        ("2400", "Sales Tax Payable", "Liability", "Tax"),
        # Revenue
        ("4000", "Product Sales", "Revenue", "Product Revenue"),
        ("4010", "Sales Returns & Allowances", "Revenue", "Contra Revenue"),
        ("4020", "Sales Discounts", "Revenue", "Contra Revenue"),
        # COGS
        ("5000", "Cost of Goods Sold", "Expense", "COGS"),
        ("5010", "Inventory Shrinkage", "Expense", "COGS"),
        ("5020", "Freight In", "Expense", "COGS"),
        # Operating Expenses
        ("5100", "Salaries & Wages", "Expense", "Payroll"),
        ("5110", "Payroll Taxes", "Expense", "Payroll"),
        ("5200", "Credit Card Processing Fees", "Expense", "Operating"),
        ("6050", "Store Supplies", "Expense", "Operating"),
        ("6060", "Security", "Expense", "Operating"),
    ],

    "E-commerce": [
        # Assets - Inventory
        ("1300", "Inventory", "Asset", "Inventory"),
        ("1310", "Inventory - FBA/3PL", "Asset", "Inventory"),
        # Liabilities
        ("2400", "Sales Tax Payable", "Liability", "Tax"),
        # Revenue
        ("4000", "Product Sales", "Revenue", "Product Revenue"),
        ("4010", "Sales Returns & Refunds", "Revenue", "Contra Revenue"),
        ("4020", "Shipping Income", "Revenue", "Other"),
        # COGS
        ("5000", "Cost of Goods Sold", "Expense", "COGS"),
        ("5010", "Shipping & Fulfillment", "Expense", "COGS"),
        ("5020", "Packaging Materials", "Expense", "COGS"),
        ("5030", "Amazon/Platform Fees", "Expense", "COGS"),
        # Operating
        ("5100", "Salaries & Wages", "Expense", "Payroll"),
        ("5110", "Payroll Taxes", "Expense", "Payroll"),
        ("5200", "Payment Processing Fees", "Expense", "Operating"),
        ("6050", "Website & Hosting", "Expense", "Operating"),
        ("6060", "Product Photography", "Expense", "Operating"),
    ],

    "Wholesale/Distribution": [
        # Assets
        ("1300", "Inventory", "Asset", "Inventory"),
        ("1310", "Inventory - In Transit", "Asset", "Inventory"),
        ("1600", "Vehicles", "Asset", "Fixed Asset"),
        ("1610", "Accumulated Depreciation - Vehicles", "Asset", "Contra Asset"),
        ("1700", "Warehouse Equipment", "Asset", "Fixed Asset"),
        ("1710", "Accumulated Depreciation - Warehouse", "Asset", "Contra Asset"),
        # Liabilities
        ("2400", "Sales Tax Payable", "Liability", "Tax"),
        # Revenue
        ("4000", "Product Sales", "Revenue", "Product Revenue"),
        ("4010", "Volume Discounts", "Revenue", "Contra Revenue"),
        # COGS
        ("5000", "Cost of Goods Sold", "Expense", "COGS"),
        ("5010", "Freight In", "Expense", "COGS"),
        ("5020", "Freight Out", "Expense", "COGS"),
        # Operating
        ("5100", "Salaries & Wages", "Expense", "Payroll"),
        ("5110", "Payroll Taxes", "Expense", "Payroll"),
        ("6050", "Warehouse Rent", "Expense", "Occupancy"),
        ("6060", "Warehouse Supplies", "Expense", "Operating"),
    ],

    "Restaurant/Food Service": [
        # Assets
        ("1300", "Food Inventory", "Asset", "Inventory"),
        ("1310", "Beverage Inventory", "Asset", "Inventory"),
        ("1320", "Supplies Inventory", "Asset", "Inventory"),
        ("1550", "Kitchen Equipment", "Asset", "Fixed Asset"),
        ("1560", "Accumulated Depreciation - Kitchen", "Asset", "Contra Asset"),
        # Liabilities
        ("2400", "Sales Tax Payable", "Liability", "Tax"),
        ("2410", "Tips Payable", "Liability", "Payable"),
        # Revenue
        ("4000", "Food Sales", "Revenue", "Product Revenue"),
        ("4010", "Beverage Sales", "Revenue", "Product Revenue"),
        ("4020", "Catering Revenue", "Revenue", "Service Revenue"),
        # COGS
        ("5000", "Cost of Food Sold", "Expense", "COGS"),
        ("5010", "Cost of Beverages Sold", "Expense", "COGS"),
        # Operating
        ("5100", "Salaries & Wages - Kitchen", "Expense", "Payroll"),
        ("5110", "Salaries & Wages - Front of House", "Expense", "Payroll"),
        ("5120", "Payroll Taxes", "Expense", "Payroll"),
        ("6050", "Smallwares & Utensils", "Expense", "Operating"),
        ("6060", "Linen & Laundry", "Expense", "Operating"),
        ("6070", "Cleaning Supplies", "Expense", "Operating"),
        ("6080", "Liquor License", "Expense", "Operating"),
    ],

    "Real Estate (Rental)": [
        # Assets
        ("1400", "Land", "Asset", "Fixed Asset"),
        ("1410", "Buildings", "Asset", "Fixed Asset"),
        ("1420", "Accumulated Depreciation - Buildings", "Asset", "Contra Asset"),
        ("1430", "Building Improvements", "Asset", "Fixed Asset"),
        ("1440", "Accumulated Depreciation - Improvements", "Asset", "Contra Asset"),
        ("1120", "Security Deposits Receivable", "Asset", "Receivable"),
        # Liabilities
        ("2500", "Mortgage Payable", "Liability", "Loan"),
        ("2510", "Security Deposits Held", "Liability", "Deposit"),
        # Revenue
        ("4000", "Rental Income", "Revenue", "Rental"),
        ("4010", "Late Fees", "Revenue", "Other"),
        ("4020", "Parking Income", "Revenue", "Rental"),
        ("4030", "Laundry Income", "Revenue", "Other"),
        # Expenses
        ("5000", "Property Management Fees", "Expense", "Operating"),
        ("6010", "Property Taxes", "Expense", "Tax"),
        ("6020", "Property Insurance", "Expense", "Operating"),
        ("6030", "Repairs & Maintenance", "Expense", "Operating"),
        ("6040", "Landscaping", "Expense", "Operating"),
        ("6050", "HOA Fees", "Expense", "Operating"),
        ("6060", "Mortgage Interest", "Expense", "Interest"),
        ("7010", "Depreciation - Buildings", "Expense", "Non-Cash"),
    ],

    "Construction/Contractor": [
        # Assets
        ("1300", "Materials Inventory", "Asset", "Inventory"),
        ("1600", "Vehicles", "Asset", "Fixed Asset"),
        ("1610", "Accumulated Depreciation - Vehicles", "Asset", "Contra Asset"),
        ("1700", "Tools & Equipment", "Asset", "Fixed Asset"),
        ("1710", "Accumulated Depreciation - Tools", "Asset", "Contra Asset"),
        ("1120", "Retainage Receivable", "Asset", "Receivable"),
        ("1130", "Costs in Excess of Billings", "Asset", "WIP"),
        # Liabilities
        ("2400", "Sales Tax Payable", "Liability", "Tax"),
        ("2500", "Retainage Payable", "Liability", "Payable"),
        ("2510", "Billings in Excess of Costs", "Liability", "Deferred"),
        # Revenue
        ("4000", "Contract Revenue", "Revenue", "Service Revenue"),
        ("4010", "Change Order Revenue", "Revenue", "Service Revenue"),
        ("4020", "T&M Revenue", "Revenue", "Service Revenue"),
        # Job Costs (Direct)
        ("5000", "Job Materials", "Expense", "Job Cost"),
        ("5010", "Job Labor", "Expense", "Job Cost"),
        ("5020", "Subcontractor Costs", "Expense", "Job Cost"),
        ("5030", "Equipment Rental", "Expense", "Job Cost"),
        ("5040", "Permits & Fees", "Expense", "Job Cost"),
        # Overhead
        ("5100", "Salaries & Wages - Office", "Expense", "Payroll"),
        ("5110", "Payroll Taxes", "Expense", "Payroll"),
        ("6050", "Vehicle Expenses", "Expense", "Operating"),
        ("6060", "Tool & Equipment Maintenance", "Expense", "Operating"),
        ("6070", "Contractor License", "Expense", "Operating"),
        ("6080", "Bonding & Insurance", "Expense", "Operating"),
    ],

    "Manufacturing": [
        # Assets
        ("1300", "Raw Materials Inventory", "Asset", "Inventory"),
        ("1310", "Work in Process Inventory", "Asset", "Inventory"),
        ("1320", "Finished Goods Inventory", "Asset", "Inventory"),
        ("1700", "Manufacturing Equipment", "Asset", "Fixed Asset"),
        ("1710", "Accumulated Depreciation - Mfg Equipment", "Asset", "Contra Asset"),
        # Liabilities
        ("2400", "Sales Tax Payable", "Liability", "Tax"),
        # Revenue
        ("4000", "Product Sales", "Revenue", "Product Revenue"),
        ("4010", "Sales Returns & Allowances", "Revenue", "Contra Revenue"),
        # COGS / Manufacturing Costs
        ("5000", "Raw Materials Used", "Expense", "COGS"),
        ("5010", "Direct Labor", "Expense", "COGS"),
        ("5020", "Manufacturing Overhead", "Expense", "COGS"),
        ("5030", "Freight In", "Expense", "COGS"),
        # Operating
        ("5100", "Salaries & Wages - Admin", "Expense", "Payroll"),
        ("5110", "Payroll Taxes", "Expense", "Payroll"),
        ("6010", "Factory Rent", "Expense", "Occupancy"),
        ("6020", "Factory Utilities", "Expense", "Occupancy"),
        ("6050", "Quality Control", "Expense", "Operating"),
        ("6060", "Equipment Maintenance", "Expense", "Operating"),
    ],

    "Personal/Individual": [
        # Revenue (Income)
        ("4000", "Salary & Wages", "Revenue", "Employment"),
        ("4010", "Interest Income", "Revenue", "Investment"),
        ("4020", "Dividend Income", "Revenue", "Investment"),
        ("4030", "Capital Gains", "Revenue", "Investment"),
        ("4040", "Rental Income", "Revenue", "Rental"),
        ("4050", "Side Income", "Revenue", "Other"),
        # Expenses (Personal)
        ("5000", "Housing - Mortgage/Rent", "Expense", "Housing"),
        ("5010", "Housing - Property Tax", "Expense", "Housing"),
        ("5020", "Housing - Insurance", "Expense", "Housing"),
        ("5030", "Utilities", "Expense", "Housing"),
        ("5100", "Groceries", "Expense", "Food"),
        ("5110", "Dining Out", "Expense", "Food"),
        ("5200", "Transportation - Auto Payment", "Expense", "Transportation"),
        ("5210", "Transportation - Gas", "Expense", "Transportation"),
        ("5220", "Transportation - Insurance", "Expense", "Transportation"),
        ("5230", "Transportation - Maintenance", "Expense", "Transportation"),
        ("5300", "Healthcare - Insurance", "Expense", "Healthcare"),
        ("5310", "Healthcare - Medical", "Expense", "Healthcare"),
        ("5320", "Healthcare - Dental", "Expense", "Healthcare"),
        ("5330", "Healthcare - Pharmacy", "Expense", "Healthcare"),
        ("5400", "Insurance - Life", "Expense", "Insurance"),
        ("5500", "Education", "Expense", "Education"),
        ("5600", "Entertainment", "Expense", "Discretionary"),
        ("5610", "Subscriptions", "Expense", "Discretionary"),
        ("5700", "Clothing", "Expense", "Discretionary"),
        ("5800", "Gifts & Donations", "Expense", "Giving"),
        ("5900", "Taxes - Federal", "Expense", "Tax"),
        ("5910", "Taxes - State", "Expense", "Tax"),
    ],

    "Other": [
        # Basic service and product accounts
        ("4000", "Service Revenue", "Revenue", "Service Revenue"),
        ("4100", "Product Sales", "Revenue", "Product Revenue"),
        # Basic expenses
        ("5000", "Salaries & Wages", "Expense", "Payroll"),
        ("5100", "Payroll Taxes", "Expense", "Payroll"),
        ("5200", "Employee Benefits", "Expense", "Payroll"),
        ("5300", "Contract Labor", "Expense", "Payroll"),
    ],
}


def get_accounts_for_client(entity_type: str, business_type: str) -> list:
    """Get the complete chart of accounts for a given entity and business type."""
    accounts = list(CORE_ACCOUNTS)

    # Add entity-specific equity accounts
    if entity_type in EQUITY_ACCOUNTS:
        accounts.extend(EQUITY_ACCOUNTS[entity_type])
    else:
        accounts.extend(EQUITY_ACCOUNTS["Sole Proprietorship"])

    # Add entity-specific non-equity accounts
    if entity_type in ENTITY_SPECIFIC_ACCOUNTS:
        accounts.extend(ENTITY_SPECIFIC_ACCOUNTS[entity_type])

    # Add business-type specific accounts
    if business_type in BUSINESS_TYPE_ACCOUNTS:
        accounts.extend(BUSINESS_TYPE_ACCOUNTS[business_type])
    else:
        accounts.extend(BUSINESS_TYPE_ACCOUNTS["Other"])

    # Remove duplicates by account number (keep first occurrence)
    seen = set()
    unique_accounts = []
    for account in accounts:
        if account[0] not in seen:
            seen.add(account[0])
            unique_accounts.append(account)

    # New books use the curated statement vocabulary even though these source
    # templates retain their older, more granular labels for compatibility and
    # readability. Known aliases are canonicalized here; nothing touches an
    # existing book until its user explicitly reviews that chart.
    unique_accounts = [
        (
            number,
            name,
            account_type,
            AccountSubtype.normalize_for_storage(
                account_type, subtype, account_name=name
            ),
        )
        for number, name, account_type, subtype in unique_accounts
    ]

    # Sort by account number
    unique_accounts.sort(key=lambda x: x[0])

    return unique_accounts


# Legacy function for backwards compatibility
def get_accounts_for_entity_type(entity_type: str) -> list:
    """Get accounts for entity type only (legacy - defaults to Professional Services)."""
    return get_accounts_for_client(entity_type, "Professional Services")


def seed_chart_of_accounts(conn: sqlite3.Connection):
    """Deprecated - use seed_chart_of_accounts_for_client()."""
    pass


def seed_chart_of_accounts_for_client(
    conn: sqlite3.Connection,
    client_id: int,
    entity_type: str = "Sole Proprietorship",
    business_type: str = "Other"
):
    """Seed the chart of accounts for a specific client based on entity and business type."""
    cursor = conn.cursor()

    # Check if this client already has accounts
    cursor.execute("SELECT COUNT(*) FROM accounts WHERE client_id = ?", (client_id,))
    count = cursor.fetchone()[0]

    if count == 0:
        accounts = get_accounts_for_client(entity_type, business_type)
        data = [(client_id, *account) for account in accounts]
        cursor.executemany(
            """
            INSERT INTO accounts (client_id, account_number, name, type, subtype)
            VALUES (?, ?, ?, ?, ?)
            """,
            data
        )
        # The caller owns the transaction so client creation and account seeding
        # commit or roll back together.
