from datetime import date, datetime

from astronomy import AstronomyCalculator

astro = AstronomyCalculator(
    latitude=48.6845,
    longitude=26.5856,
    timezone="Europe/Kyiv",
)


sun = astro.sun_times(date(2026, 8, 29))

print(sun.sunrise)
print(sun.sunset)
print(sun.civil_dawn)
print(sun.civil_dusk)
print(sun.daylight_duration)


moon = astro.moon_phase(date(2026, 8, 29))

print(moon.name)
print(moon.age_days)
print(moon.illumination_percent)

print(moon.moonrise)
print(moon.moonset)


dt = datetime(
    2026,
    8,
    29,
    20,
    30,
)

print(astro.sun_altitude(dt))
print(astro.sun_azimuth(dt))

print(astro.moon_altitude(dt))
print(astro.moon_azimuth(dt))


data = astro.get_current_data(datetime(2026, 8, 29, 20, 30))

print(data)

data = astro.get_day_data(date(2026, 8, 29))

print(data)
