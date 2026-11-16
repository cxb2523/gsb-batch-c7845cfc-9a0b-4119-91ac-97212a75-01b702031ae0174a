"""
Congressional Trade Scraper Service.

Orchestrates fetching congressional trades from APIs and saving to database.
"""

import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models.company import Company
from app.models.congressperson import Congressperson, Chamber, Party
from app.models.congressional_trade import CongressionalTrade
from app.services.congressional_client import CongressionalAPIClient

logger = logging.getLogger(__name__)


class CongressionalScraperService:
    """Service for scraping congressional stock trades."""

    def __init__(self, api_client: Optional[CongressionalAPIClient] = None):
        """Initialize scraper service."""
        self.api_client = api_client or CongressionalAPIClient()

    async def scrape_congressional_trades(
        self,
        db: AsyncSession,
        ticker: Optional[str] = None,
        chamber: Optional[str] = None,
        days_back: int = 60,
    ) -> Dict[str, Any]:
        """
        Scrape congressional trades from Finnhub API.

        Args:
            db: Database session
            ticker: Stock ticker (optional, fetches all if None)
            chamber: HOUSE or SENATE (optional)
            days_back: Days to look back

        Returns:
            Summary of scraping results. Keys:
                success: False only when the API fetch itself failed.
                trades_processed: Records handled without an error (created +
                    skipped).
                trades_created: Newly inserted trades.
                trades_skipped: Records skipped by existing logic
                    (duplicates, or an unresolvable congressperson).
                    Not counted as failures.
                trades_failed: Number of records that raised while writing.
                failed_trades: List of per-record failure details, each with
                    index, name, ticker, transaction_date and reason for
                    locating the offending row.
                errors: Failure reason strings (kept for backward
                    compatibility).
        """
        logger.info(
            f"Starting congressional trade scrape: ticker={ticker}, chamber={chamber}, days_back={days_back}"
        )

        to_date = date.today()
        from_date = to_date - timedelta(days=days_back)

        # Fetch trades from API
        try:
            trades_data = await self.api_client.fetch_congressional_trades(
                symbol=ticker, from_date=from_date, to_date=to_date
            )
        except Exception as e:
            logger.error(f"Failed to fetch congressional trades: {e}")
            return {
                "success": False,
                "error": str(e),
                "trades_processed": 0,
                "trades_created": 0,
                "trades_skipped": 0,
                "trades_failed": 0,
                "failed_trades": [],
            }

        if not trades_data:
            logger.info("No congressional trades found")
            return {
                "success": True,
                "trades_processed": 0,
                "trades_created": 0,
                "trades_skipped": 0,
                "trades_failed": 0,
                "failed_trades": [],
                "message": "No trades found",
            }

        # Filter by chamber if specified
        if chamber:
            trades_data = [
                t for t in trades_data if t.get("chamber") == chamber.upper()
            ]

        # Process each trade. Every trade is committed independently so that a
        # bad record only rolls back itself and never discards trades that were
        # already persisted earlier in the batch.
        trades_created = 0
        trades_skipped = 0
        trades_processed = 0
        errors = []
        failed_trades = []

        for index, trade_data in enumerate(trades_data):
            try:
                created = await self._process_trade(db, trade_data)
                # Independent commit per trade: once this returns, the trade is
                # durable and cannot be lost by a later record's failure.
                await db.commit()
            except Exception as e:
                reason = str(e)
                logger.error(
                    "Failed to process congressional trade "
                    f"(index={index}, name={trade_data.get('name')!r}, "
                    f"ticker={trade_data.get('ticker')!r}): {e}",
                    exc_info=True,
                )
                try:
                    # Roll back only this trade's uncommitted changes; earlier
                    # trades were already committed and remain intact.
                    await db.rollback()
                except Exception as rollback_error:  # pragma: no cover - defensive
                    logger.error(
                        "Failed to roll back congressional trade "
                        f"(index={index}): {rollback_error}"
                    )

                errors.append(reason)
                failed_trades.append(
                    {
                        # Identifiers let callers locate the offending row.
                        "index": index,
                        "name": trade_data.get("name"),
                        "ticker": trade_data.get("ticker"),
                        "transaction_date": trade_data.get("transaction_date"),
                        "reason": reason,
                    }
                )
                continue

            trades_processed += 1
            if created:
                trades_created += 1
            else:
                trades_skipped += 1

        logger.info(
            f"Congressional scrape complete: {trades_processed} processed "
            f"({trades_created} created, {trades_skipped} skipped), "
            f"{len(failed_trades)} failed"
        )

        return {
            "success": True,
            "trades_processed": trades_processed,
            "trades_created": trades_created,
            "trades_skipped": trades_skipped,
            "trades_failed": len(failed_trades),
            "trades_updated": trades_skipped,
            "failed_trades": failed_trades,
            "errors": errors if errors else None,
        }

    async def _process_trade(
        self, db: AsyncSession, trade_data: Dict[str, Any]
    ) -> bool:
        """
        Process a single congressional trade.

        Args:
            db: Database session
            trade_data: Trade data from API

        Returns:
            True if new trade created, False if duplicate
        """
        # Ensure congressperson exists
        congressperson = await self._ensure_congressperson(db, trade_data)
        if not congressperson:
            logger.warning(
                f"Could not create/find congressperson for: {trade_data.get('name')}"
            )
            return False

        # Ensure company exists (if ticker available)
        company = None
        ticker = trade_data.get("ticker")
        if ticker and ticker != "--":
            company = await self._ensure_company(
                db, ticker, trade_data.get("asset_description")
            )

        # Check for duplicate
        existing = await self._find_duplicate(
            db,
            congressperson.id,
            trade_data.get("transaction_date"),
            ticker,
            trade_data.get("amount_estimated"),
        )

        if existing:
            logger.debug(
                f"Duplicate trade found, skipping: {trade_data.get('name')} - {ticker}"
            )
            return False

        # Create trade
        trade = CongressionalTrade(
            congressperson_id=congressperson.id,
            company_id=company.id if company else None,
            transaction_date=datetime.strptime(
                trade_data["transaction_date"], "%Y-%m-%d"
            ).date(),
            disclosure_date=datetime.strptime(
                trade_data["disclosure_date"], "%Y-%m-%d"
            ).date(),
            transaction_type=trade_data["transaction_type"],
            ticker=ticker if ticker and ticker != "--" else None,
            asset_description=trade_data.get("asset_description", ""),
            amount_min=Decimal(str(trade_data["amount_min"]))
            if trade_data.get("amount_min")
            else None,
            amount_max=Decimal(str(trade_data["amount_max"]))
            if trade_data.get("amount_max")
            else None,
            amount_estimated=Decimal(str(trade_data["amount_estimated"]))
            if trade_data.get("amount_estimated")
            else None,
            is_range_estimate=True,
            owner_type=trade_data.get("owner_type", "Self"),
            asset_type=trade_data.get("asset_type", "Stock"),
            source=trade_data.get("source", "finnhub"),
        )

        db.add(trade)
        logger.debug(
            f"Created congressional trade: {congressperson.name} - {ticker} - {trade_data['transaction_type']}"
        )
        return True

    async def _ensure_congressperson(
        self, db: AsyncSession, trade_data: Dict[str, Any]
    ) -> Optional[Congressperson]:
        """Get or create congressperson from trade data."""
        name = trade_data.get("name", "").strip()
        if not name:
            return None

        # Extract chamber
        chamber_str = trade_data.get("chamber", "HOUSE").upper()
        if chamber_str not in [Chamber.HOUSE.value, Chamber.SENATE.value]:
            chamber_str = Chamber.HOUSE.value

        # Try to find by name first
        result = await db.execute(
            select(Congressperson).where(
                Congressperson.name == name, Congressperson.chamber == chamber_str
            )
        )
        congressperson = result.scalar_one_or_none()

        if congressperson:
            return congressperson

        # Parse name for first/last
        parts = name.replace("Rep.", "").replace("Sen.", "").strip().split()
        first_name = parts[0] if len(parts) > 0 else None
        last_name = (
            parts[-1] if len(parts) > 1 else parts[0] if len(parts) == 1 else name
        )

        # Create new congressperson
        try:
            congressperson = Congressperson(
                name=name,
                first_name=first_name,
                last_name=last_name,
                chamber=chamber_str,
                state="XX",  # Unknown state - would need additional enrichment
                party=Party.OTHER.value,  # Unknown party - would need additional enrichment
                active=True,
            )
            db.add(congressperson)
            await db.flush()  # Get ID without committing
            logger.info(f"Created congressperson: {name}")
            return congressperson

        except IntegrityError:
            await db.rollback()
            # Try to fetch again (race condition)
            result = await db.execute(
                select(Congressperson).where(
                    Congressperson.name == name, Congressperson.chamber == chamber_str
                )
            )
            return result.scalar_one_or_none()

    async def _ensure_company(
        self, db: AsyncSession, ticker: str, asset_description: Optional[str] = None
    ) -> Optional[Company]:
        """Get or create company from ticker."""
        ticker = ticker.upper().strip()

        # Try to find existing company
        result = await db.execute(select(Company).where(Company.ticker == ticker))
        company = result.scalar_one_or_none()

        if company:
            return company

        # Create placeholder company (would need enrichment for full details)
        try:
            company = Company(
                ticker=ticker,
                name=asset_description or ticker,
                cik="0000000000",  # Placeholder CIK
            )
            db.add(company)
            await db.flush()
            logger.info(f"Created placeholder company: {ticker}")
            return company

        except IntegrityError:
            await db.rollback()
            # Try to fetch again
            result = await db.execute(select(Company).where(Company.ticker == ticker))
            return result.scalar_one_or_none()

    async def _find_duplicate(
        self,
        db: AsyncSession,
        congressperson_id: int,
        transaction_date: str,
        ticker: Optional[str],
        amount_estimated: Optional[float],
    ) -> Optional[CongressionalTrade]:
        """Check if trade already exists."""
        try:
            trans_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        except Exception:
            return None

        query = select(CongressionalTrade).where(
            CongressionalTrade.congressperson_id == congressperson_id,
            CongressionalTrade.transaction_date == trans_date,
        )

        if ticker:
            query = query.where(CongressionalTrade.ticker == ticker.upper())

        if amount_estimated:
            # Match trades within $100 (for rounding differences)
            query = query.where(
                CongressionalTrade.amount_estimated.between(
                    Decimal(str(amount_estimated)) - 100,
                    Decimal(str(amount_estimated)) + 100,
                )
            )

        result = await db.execute(query)
        return result.scalar_one_or_none()
