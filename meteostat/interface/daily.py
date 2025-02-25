"""
Daily Class

Meteorological data provided by Meteostat (https://dev.meteostat.net)
under the terms of the Creative Commons Attribution-NonCommercial
4.0 International Public License.

The code is licensed under the MIT license.
"""

from datetime import datetime
from typing import Union
import numpy as np
import pandas as pd
from meteostat.core.cache import get_local_file_path, file_in_cache
from meteostat.core.loader import processing_handler, load_handler
from meteostat.enumerations.granularity import Granularity
from meteostat.utilities.validations import validate_series
from meteostat.utilities.aggregations import degree_mean, weighted_average
from meteostat.utilities.endpoint import generate_endpoint_path
from meteostat.interface.timeseries import Timeseries
from meteostat.interface.point import Point


class Daily(Timeseries):

    """
    Retrieve daily weather observations for one or multiple weather stations or
    a single geographical point
    """

    # The cache subdirectory
    cache_subdir: str = 'daily'

    # Default frequency
    _freq: str = '1D'

    # Columns
    _columns: list = [
        'date',
        'tavg',
        'tmin',
        'tmax',
        'prcp',
        'snow',
        'wdir',
        'wspd',
        'wpgt',
        'pres',
        'tsun'
    ]

    # Index of first meteorological column
    _first_met_col = 1

    # Data types
    _types: dict = {
        'tavg': 'float64',
        'tmin': 'float64',
        'tmax': 'float64',
        'prcp': 'float64',
        'snow': 'float64',
        'wdir': 'float64',
        'wspd': 'float64',
        'wpgt': 'float64',
        'pres': 'float64',
        'tsun': 'float64'
    }

    # Columns for date parsing
    _parse_dates: dict = {
        'time': [0]
    }

    # Default aggregation functions
    aggregations: dict = {
        'tavg': 'mean',
        'tmin': 'min',
        'tmax': 'max',
        'prcp': 'sum',
        'snow': 'max',
        'wdir': degree_mean,
        'wspd': 'mean',
        'wpgt': 'max',
        'pres': 'mean',
        'tsun': 'sum'
    }

    def _load(
        self,
        station: str
    ) -> None:
        """
        Load file for a single station from Meteostat
        """

        # File name
        file = generate_endpoint_path(
            Granularity.DAILY,
            station,
            self._model
        )

        # Get local file path
        path = get_local_file_path(self.cache_dir, self.cache_subdir, file)

        # Check if file in cache
        if self.max_age > 0 and file_in_cache(path, self.max_age):

            # Read cached data
            df = pd.read_pickle(path)

        else:

            # Get data from Meteostat
            df = load_handler(
                self.endpoint,
                file,
                self._columns,
                self._types,
                self._parse_dates)

            # Validate Series
            df = validate_series(df, station)

            # Save as Pickle
            if self.max_age > 0:
                df.to_pickle(path)

        # Filter time period and append to DataFrame
        if self._start and self._end:

            # Get time index
            time = df.index.get_level_values('time')

            # Filter & return
            return df.loc[(time >= self._start) & (time <= self._end)]

        # Return
        return df

    def _get_data(self) -> None:
        """
        Get all required data
        """

        if len(self._stations) > 0:

            # List of datasets
            datasets = [(str(station),) for station in self._stations]

            # Data Processing
            return processing_handler(
                datasets, self._load, self.processes, self.threads)

        # Empty DataFrame
        return pd.DataFrame(columns=[*self._types])

    def _resolve_point(
        self,
        method: str,
        stations: pd.DataFrame,
        alt: int,
        adapt_temp: bool
    ) -> None:
        """
        Project weather station data onto a single point
        """

        if self._stations.size == 0 or self._data.size == 0:
            return None

        def adjust_temp(data: pd.DataFrame):
            """
            Adjust temperature-like data based on altitude
            """

            data.loc[data['tavg'] != np.NaN, 'tavg'] = data['tavg'] + \
                ((2 / 3) * ((data['elevation'] - alt) / 100))
            data.loc[data['tmin'] != np.NaN, 'tmin'] = data['tmin'] + \
                ((2 / 3) * ((data['elevation'] - alt) / 100))
            data.loc[data['tmax'] != np.NaN, 'tmax'] = data['tmax'] + \
                ((2 / 3) * ((data['elevation'] - alt) / 100))

            return data

        if method == 'nearest':

            if adapt_temp:

                # Join elevation of involved weather stations
                data = self._data.join(
                    stations['elevation'], on='station')

                # Adapt temperature-like data based on altitude
                data = adjust_temp(data)

                # Drop elevation & round
                data = data.drop('elevation', axis=1).round(1)

            else:

                data = self._data

            self._data = data.groupby(
                pd.Grouper(level='time', freq=self._freq)).agg('first')

        else:

            # Join score and elevation of involved weather stations
            data = self._data.join(
                stations[['score', 'elevation']], on='station')

            # Adapt temperature-like data based on altitude
            if adapt_temp:
                data = adjust_temp(data)

            # Exclude non-mean data & perform aggregation
            excluded = data['wdir']
            excluded = excluded.groupby(
                pd.Grouper(level='time', freq=self._freq)).agg('first')

            # Aggregate mean data
            data = data.groupby(
                pd.Grouper(level='time', freq=self._freq)).apply(weighted_average)

            # Drop RangeIndex
            data.index = data.index.droplevel(1)

            # Merge excluded fields
            data['wdir'] = excluded

            # Drop score and elevation
            self._data = data.drop(['score', 'elevation'], axis=1).round(1)

        # Set placeholder station ID
        self._data['station'] = 'XXXXX'
        self._data = self._data.set_index(
            ['station', self._data.index.get_level_values('time')])
        self._stations = pd.Index(['XXXXX'])

    def __init__(
        self,
        loc: Union[pd.DataFrame, Point, list, str],
        start: datetime = None,
        end: datetime = None,
        model: bool = True
    ) -> None:

        # Set list of weather stations
        if isinstance(loc, pd.DataFrame):
            self._stations = loc.index
        elif isinstance(loc, Point):
            stations = loc.get_stations('daily', start, end, model)
            self._stations = stations.index
        else:
            if not isinstance(loc, list):
                loc = [loc]

            self._stations = pd.Index(loc)

        # Set start date
        self._start = start

        # Set end date
        self._end = end

        # Set model
        self._model = model

        # Get data for all weather stations
        self._data = self._get_data()

        # Interpolate data
        if isinstance(loc, Point):
            self._resolve_point(loc.method, stations, loc.alt, loc.adapt_temp)

        # Clear cache
        if self.max_age > 0 and self.autoclean:
            self.clear_cache()

    def expected_rows(self) -> int:
        """
        Return the number of rows expected for the defined date range
        """

        return (self._end - self._start).days + 1
