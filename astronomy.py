from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from astral import Observer, moon
from astral.sun import (
    azimuth as sun_azimuth,
    dawn,
    dusk,
    elevation as sun_elevation,
    noon,
    sunrise,
    sunset,
)
from astral.moon import azimuth as moon_azimuth
from astral.moon import elevation as moon_elevation
from astral.moon import moonrise, moonset

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNODIC_MONTH = 29.530588853

# Astral uses approximately a 28-day phase scale:
#
#   0  = New Moon
#   7  = First Quarter
#   14 = Full Moon
#   21 = Last Quarter
#
# We convert it to the real synodic month when calculating age.
ASTRAL_PHASE_SCALE = 28.0


# ---------------------------------------------------------------------------
# SunTimes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SunTimes:
    """
    Solar events for a particular date and location.

    All datetime values are timezone-aware.

    Some events can be None. For example, at high latitudes the
    Sun may not rise or set on a particular date.
    """

    date: date

    astronomical_dawn: Optional[datetime]
    nautical_dawn: Optional[datetime]
    civil_dawn: Optional[datetime]

    sunrise: Optional[datetime]
    solar_noon: Optional[datetime]
    sunset: Optional[datetime]

    civil_dusk: Optional[datetime]
    nautical_dusk: Optional[datetime]
    astronomical_dusk: Optional[datetime]

    @property
    def daylight_duration(self) -> Optional[float]:
        """
        Daylight duration in hours.
        """
        if self.sunrise is None or self.sunset is None:
            return None

        return (self.sunset - self.sunrise).total_seconds() / 3600.0

    @property
    def daylight_duration_minutes(self) -> Optional[int]:
        """
        Daylight duration in whole minutes.
        """
        if self.sunrise is None or self.sunset is None:
            return None

        return round((self.sunset - self.sunrise).total_seconds() / 60.0)

    def phase_at(
        self,
        value: datetime | time | str,
    ) -> str:
        """
        Determine the solar phase at a particular local time.

        Supported values:

            datetime
            time
            "HH:MM"
            "HH:MM:SS"

        Returns:

            night
            astronomical_twilight
            nautical_twilight
            civil_twilight
            daylight
        """

        current = self._normalize_datetime(value)

        # Before astronomical dawn
        if self.astronomical_dawn is not None and current < self.astronomical_dawn:
            return "night"

        # Astronomical dawn -> nautical dawn
        if (
            self.astronomical_dawn is not None
            and self.nautical_dawn is not None
            and current < self.nautical_dawn
        ):
            return "astronomical_twilight"

        # Nautical dawn -> civil dawn
        if (
            self.nautical_dawn is not None
            and self.civil_dawn is not None
            and current < self.civil_dawn
        ):
            return "nautical_twilight"

        # Civil dawn -> sunrise
        if (
            self.civil_dawn is not None
            and self.sunrise is not None
            and current < self.sunrise
        ):
            return "civil_twilight"

        # Sunrise -> sunset
        if (
            self.sunrise is not None
            and self.sunset is not None
            and current < self.sunset
        ):
            return "daylight"

        # Sunset -> civil dusk
        if (
            self.sunset is not None
            and self.civil_dusk is not None
            and current < self.civil_dusk
        ):
            return "civil_twilight"

        # Civil dusk -> nautical dusk
        if (
            self.civil_dusk is not None
            and self.nautical_dusk is not None
            and current < self.nautical_dusk
        ):
            return "nautical_twilight"

        # Nautical dusk -> astronomical dusk
        if (
            self.nautical_dusk is not None
            and self.astronomical_dusk is not None
            and current < self.astronomical_dusk
        ):
            return "astronomical_twilight"

        return "night"

    def _normalize_datetime(
        self,
        value: datetime | time | str,
    ) -> datetime:

        if isinstance(value, str):
            value = time.fromisoformat(value)

        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("datetime must be timezone-aware")

            return value

        if isinstance(value, time):
            tz = self._get_timezone()

            return datetime.combine(
                self.date,
                value.replace(tzinfo=None),
            ).replace(tzinfo=tz)

        raise TypeError("value must be datetime, time or HH:MM string")

    def _get_timezone(self) -> ZoneInfo:
        for event in (
            self.sunrise,
            self.sunset,
            self.solar_noon,
            self.civil_dawn,
            self.civil_dusk,
        ):
            if event is not None and event.tzinfo is not None:
                return event.tzinfo

        return ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# MoonPhase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MoonPhase:
    """
    Moon phase information for a particular date.

    phase:
        Astral's 0..28 phase value.

    name:
        One of the eight traditional Moon phases.

    age_days:
        Approximate age of the current lunar cycle.

    illumination:
        Illuminated fraction from 0.0 to 1.0.

    moonrise:
        Local moonrise time for the date.

    moonset:
        Local moonset time for the date.
    """

    date: date

    phase: float
    name: str

    age_days: float
    illumination: float

    moonrise: Optional[datetime]
    moonset: Optional[datetime]

    @property
    def illumination_percent(self) -> float:
        return self.illumination * 100.0


# ---------------------------------------------------------------------------
# AstronomyCalculator
# ---------------------------------------------------------------------------


class AstronomyCalculator:
    """
    Calculate solar and lunar astronomical information.

    Parameters
    ----------
    latitude:
        Latitude in degrees.
        North is positive, South is negative.

    longitude:
        Longitude in degrees.
        East is positive, West is negative.

    timezone:
        IANA timezone name.

        Examples:

            Europe/Kyiv
            Europe/London
            America/New_York
            UTC
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        timezone: str = "UTC",
    ):
        if not -90 <= latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")

        if not -180 <= longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")

        try:
            self.timezone = ZoneInfo(timezone)
        except Exception as exc:
            raise ValueError(f"Invalid timezone: {timezone}") from exc

        self.latitude = float(latitude)
        self.longitude = float(longitude)

        self.observer = Observer(
            latitude=self.latitude,
            longitude=self.longitude,
        )

    # =======================================================================
    # SUN
    # =======================================================================

    def sun_times(
        self,
        target_date: date,
    ) -> SunTimes:
        """
        Calculate all important solar events for a date.

        Returns timezone-aware local datetimes.
        """

        self._validate_date(target_date)

        return SunTimes(
            date=target_date,
            # Sun 18 degrees below horizon
            astronomical_dawn=self._safe_dawn(
                target_date,
                depression=18,
            ),
            # Sun 12 degrees below horizon
            nautical_dawn=self._safe_dawn(
                target_date,
                depression=12,
            ),
            # Sun 6 degrees below horizon
            civil_dawn=self._safe_dawn(
                target_date,
                depression=6,
            ),
            sunrise=self._safe_sun_event(
                sunrise,
                target_date,
            ),
            solar_noon=self._safe_sun_event(
                noon,
                target_date,
            ),
            sunset=self._safe_sun_event(
                sunset,
                target_date,
            ),
            civil_dusk=self._safe_dusk(
                target_date,
                depression=6,
            ),
            nautical_dusk=self._safe_dusk(
                target_date,
                depression=12,
            ),
            astronomical_dusk=self._safe_dusk(
                target_date,
                depression=18,
            ),
        )

    def _safe_sun_event(
        self,
        function,
        target_date: date,
    ) -> Optional[datetime]:

        try:
            return function(
                observer=self.observer,
                date=target_date,
                tzinfo=self.timezone,
            )
        except ValueError:
            return None

    def _safe_dawn(
        self,
        target_date: date,
        depression: float,
    ) -> Optional[datetime]:

        try:
            return dawn(
                observer=self.observer,
                date=target_date,
                depression=depression,
                tzinfo=self.timezone,
            )
        except ValueError:
            return None

    def _safe_dusk(
        self,
        target_date: date,
        depression: float,
    ) -> Optional[datetime]:

        try:
            return dusk(
                observer=self.observer,
                date=target_date,
                depression=depression,
                tzinfo=self.timezone,
            )
        except ValueError:
            return None

    # =======================================================================
    # MOON
    # =======================================================================

    def moon_phase(
        self,
        target_date: date,
    ) -> MoonPhase:
        """
        Calculate Moon phase, illumination, moonrise and moonset.
        """

        self._validate_date(target_date)

        astral_phase = float(moon.phase(target_date))

        # Convert Astral 0..28 scale into real lunar age.
        age_days = (astral_phase / ASTRAL_PHASE_SCALE) * SYNODIC_MONTH

        age_days %= SYNODIC_MONTH

        name = self._moon_phase_name(age_days)

        illumination = self._moon_illumination(age_days)

        return MoonPhase(
            date=target_date,
            phase=astral_phase,
            name=name,
            age_days=age_days,
            illumination=illumination,
            moonrise=self._safe_moon_event(
                moonrise,
                target_date,
            ),
            moonset=self._safe_moon_event(
                moonset,
                target_date,
            ),
        )

    def _safe_moon_event(
        self,
        function,
        target_date: date,
    ) -> Optional[datetime]:

        try:
            return function(
                observer=self.observer,
                date=target_date,
                tzinfo=self.timezone,
            )
        except ValueError:
            return None

    @staticmethod
    def _moon_phase_name(
        age_days: float,
    ) -> str:
        """
        Determine one of eight traditional Moon phases.
        """

        phase_angle = (age_days / SYNODIC_MONTH) * 360.0

        sector = (phase_angle + 22.5) % 360.0

        sector_index = int(sector // 45.0)

        phases = (
            "New Moon",
            "Waxing Crescent",
            "First Quarter",
            "Waxing Gibbous",
            "Full Moon",
            "Waning Gibbous",
            "Last Quarter",
            "Waning Crescent",
        )

        return phases[sector_index]

    @staticmethod
    def _moon_illumination(
        age_days: float,
    ) -> float:
        """
        Estimate illuminated fraction.

        0.0 = New Moon
        0.5 = Quarter
        1.0 = Full Moon
        """

        angle = (age_days / SYNODIC_MONTH) * 2.0 * math.pi

        illumination = (1.0 - math.cos(angle)) / 2.0

        return max(
            0.0,
            min(1.0, illumination),
        )

    # =======================================================================
    # CURRENT SUN / MOON POSITION
    # =======================================================================

    def sun_altitude(
        self,
        dt: datetime,
    ) -> float:
        """
        Return the Sun's altitude above/below the horizon in degrees.

        Positive:
            Sun above horizon.

        Negative:
            Sun below horizon.
        """

        dt = self._normalize_datetime(dt)

        return float(
            sun_elevation(
                observer=self.observer,
                dateandtime=dt,
            )
        )

    def sun_azimuth(
        self,
        dt: datetime,
    ) -> float:
        """
        Return the Sun's azimuth in degrees.

        0   = North
        90  = East
        180 = South
        270 = West
        """

        dt = self._normalize_datetime(dt)

        return float(
            sun_azimuth(
                observer=self.observer,
                dateandtime=dt,
            )
        )

    def moon_altitude(
        self,
        dt: datetime,
    ) -> float:
        """
        Return the Moon's altitude above/below the horizon in degrees.

        Positive:
            Moon above horizon.

        Negative:
            Moon below horizon.
        """

        dt = self._normalize_datetime(dt)

        return float(
            moon_elevation(
                self.observer,
                dt,
            )
        )

    def moon_azimuth(
        self,
        dt: datetime,
    ) -> float:
        """
        Return the Moon's azimuth in degrees.

        0   = North
        90  = East
        180 = South
        270 = West
        """

        dt = self._normalize_datetime(dt)

        return float(
            moon_azimuth(
                self.observer,
                dt,
            )
        )

    # =======================================================================
    # CURRENT DATA
    # =======================================================================

    def get_current_data(
        self,
        dt: datetime,
    ) -> dict:
        """
        Calculate the complete astronomical state for a specific moment.

        Includes:

            Sun:
                altitude
                azimuth
                solar phase

            Moon:
                altitude
                azimuth
                phase
                illumination

        The returned dictionary is JSON-friendly.
        """

        dt = self._normalize_datetime(dt)

        target_date = dt.date()

        sun_data = self.sun_times(target_date)

        moon_data = self.moon_phase(target_date)

        return {
            "datetime": dt.isoformat(),
            "sun": {
                "altitude": self.sun_altitude(dt),
                "azimuth": self.sun_azimuth(dt),
                "phase": sun_data.phase_at(dt),
                "sunrise": self._format_datetime(sun_data.sunrise),
                "sunset": self._format_datetime(sun_data.sunset),
            },
            "moon": {
                "altitude": self.moon_altitude(dt),
                "azimuth": self.moon_azimuth(dt),
                "phase": moon_data.name,
                "phase_value": moon_data.phase,
                "age_days": moon_data.age_days,
                "illumination": moon_data.illumination,
                "illumination_percent": (moon_data.illumination_percent),
                "moonrise": self._format_datetime(moon_data.moonrise),
                "moonset": self._format_datetime(moon_data.moonset),
            },
        }

    # =======================================================================
    # DAY DATA
    # =======================================================================

    def get_day_data(
        self,
        target_date: date,
    ) -> dict:
        """
        Return complete Sun and Moon information for a date.

        Useful for APIs and database storage.
        """

        sun_data = self.sun_times(target_date)

        moon_data = self.moon_phase(target_date)

        return {
            "date": target_date.isoformat(),
            "sun": {
                "astronomical_dawn": self._format_datetime(sun_data.astronomical_dawn),
                "nautical_dawn": self._format_datetime(sun_data.nautical_dawn),
                "civil_dawn": self._format_datetime(sun_data.civil_dawn),
                "sunrise": self._format_datetime(sun_data.sunrise),
                "solar_noon": self._format_datetime(sun_data.solar_noon),
                "sunset": self._format_datetime(sun_data.sunset),
                "civil_dusk": self._format_datetime(sun_data.civil_dusk),
                "nautical_dusk": self._format_datetime(sun_data.nautical_dusk),
                "astronomical_dusk": self._format_datetime(sun_data.astronomical_dusk),
                "daylight_hours": (sun_data.daylight_duration),
                "daylight_minutes": (sun_data.daylight_duration_minutes),
            },
            "moon": {
                "phase": moon_data.name,
                "phase_value": moon_data.phase,
                "age_days": moon_data.age_days,
                "illumination": moon_data.illumination,
                "illumination_percent": (moon_data.illumination_percent),
                "moonrise": self._format_datetime(moon_data.moonrise),
                "moonset": self._format_datetime(moon_data.moonset),
            },
        }

    # =======================================================================
    # Helpers
    # =======================================================================

    def _normalize_datetime(
        self,
        dt: datetime,
    ) -> datetime:
        """
        Convert datetime to the calculator's timezone.

        Naive datetime is interpreted as local time in self.timezone.
        """

        if not isinstance(dt, datetime):
            raise TypeError("dt must be datetime")

        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.timezone)

        return dt.astimezone(self.timezone)

    @staticmethod
    def _format_datetime(
        value: Optional[datetime],
    ) -> Optional[str]:

        if value is None:
            return None

        return value.isoformat()

    @staticmethod
    def _validate_date(
        value: date,
    ) -> None:

        if not isinstance(value, date):
            raise TypeError("target_date must be datetime.date")
