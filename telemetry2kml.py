import csv
import sys
from pprint import pprint

import numpy as np
import yaml

class Telemetry2kmlConverter(object):
    """
    Telemetry2kmlConverter class definition
    """

    def __init__(self, settings_file='telemetry2kml_settings.yml', debug=False):
        """
        Constructor for Telemetry2kmlConverter class
        @param settings_file: Settings file. Default is 'telemetry2kml_settings.yml'
        @param debug: Boolean parameter used to turn debug output on/off
        """
        self.data = []
        with open(settings_file, 'r') as stream:
            settings = yaml.load(stream, Loader=yaml.Loader)
        print(settings)

        self.field_list = settings['telemetry_settings']['field_list']
        self.field_map = settings['telemetry_settings']['field_map']
        self.xyzLimit = settings['telemetry_settings']['xyzLimit']
        self.xyzRounding = settings['telemetry_settings']['xyzRounding']
        self.validSatRange = settings['telemetry_settings']['validSatRange']

    def remap_fieldnames(self, fieldnames):
        """
        Remaps field names from CSV to position-sensitive non-duplicated names
        """
        new_fieldnames = []
        field_list = list(self.field_list)
        field_map = list(self.field_map)

        # print(field_list)
        # print(fieldnames)

        for field in fieldnames:
            print(field)
            # Skip any field names not in CSV
            while field != field_list[0]:
                field_list.pop(0)
                field_map.pop(0)

            new_fieldnames.append(field_map.pop(0))
            field_list.pop(0)

        return new_fieldnames

    def read(self, csv_filename):
        """
        Reads data from CSV file into list of dicts
        """
        with open(csv_filename, 'r') as csvfile:
            reader = csv.reader(csvfile)
            fieldnames = self.remap_fieldnames(next(reader))
            print(fieldnames)

            self.data = [dict(zip(fieldnames, row)) for row in reader]

        # pprint(self.data)

    def set_coordinates(self):
        """
        Set [lat, long, elev] coordinates after repairing bad GPS data by
            1) discarding anything with a low or high satellite count
            2) calculating the median lat & long values
            3) discarding aything further than limits from any of the median coordinate values
        """

        # Only use Coordinatess from records with a valid Sats value
        for record in self.data:
            record["Sats"] = int(record["Sats"])
            record["GPS Alt(m)"] = float(record["GPS Alt(m)"])

            record["Coordinates"] = None
            if self.validSatRange[0] < record["Sats"] < self.validSatRange[1]:
                record["Coordinates"] = ([float(ordinate) for ordinate in record["GPS"].split(' ')] +
                                         [record["GPS Alt(m)"]])

        median_coords = [
            np.median(np.array([record["Coordinates"][coord_index]
                                for record in self.data if record["Coordinates"]]))
            for coord_index in range(3)
        ]

        print(median_coords)

        empty_start_index = 0
        empty_end_index = 0
        for index, record in enumerate(self.data):
            # Discard any coordinates outside the allowable range from the median
            if any(
                    [
                        record['Coordinates'] and
                        abs(record['Coordinates'][coord_index] - median_coords[coord_index]) >= self.xyzLimit[coord_index]
                        for coord_index in range(3)
                    ]
            ):
                record['Coordinates'] = None

            if record['Coordinates'] == None:
                if not empty_start_index:
                    empty_start_index = index
            else:  # Valid coordinates
                record["Interpolated"] = False
                if not empty_end_index:
                    empty_end_index = index

                # TODO: Interpolate data at start and end
                if empty_start_index and empty_end_index:  # Empty range bounded by good coordinates
                    start_coords = self.data[empty_start_index - 1]['Coordinates']
                    end_coords = self.data[empty_end_index]['Coordinates']
                    empty_count = empty_end_index - empty_start_index

                    print(start_coords, end_coords)
                    for empty_index in range(empty_count):
                        self.data[empty_start_index + empty_index]['Coordinates'] = [
                            round(start_coords[coord_index] + (
                                    (end_coords[coord_index] - start_coords[coord_index]) *
                                    (empty_index + 1) / float(empty_count + 1)
                            ), self.xyzRounding[coord_index])
                            for coord_index in range(3)
                        ]
                        self.data[empty_start_index + empty_index]["Interpolated"] = True
                        print(
                            f'empty_index: {empty_index}, Coordinates: {self.data[empty_start_index + empty_index]['Coordinates']}')
                empty_start_index = 0
                empty_end_index = 0

            print(f'index: {index}, Sats: {record["Sats"]}, Coordinates: {record["Coordinates"]}')


if __name__ == '__main__':
    csv_file = sys.argv[1]
    converter = Telemetry2kmlConverter()
    converter.read(csv_file)
    converter.set_coordinates()
    pprint([[record["Coordinates"], record["Interpolated"]] for record in converter.data])
