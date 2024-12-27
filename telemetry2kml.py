import csv
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import simplekml
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
        self.input_csv_path = None
        self.coordinate_ranges = None
        self.data = []
        with open(settings_file, 'r') as stream:
            settings = yaml.load(stream, Loader=yaml.Loader)
        # print(settings)

        self.field_list = settings['telemetry_settings']['field_list']
        self.field_map = settings['telemetry_settings']['field_map']
        self.displayed_fields = settings['telemetry_settings']['displayed_fields']
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
            # print(field)
            # Skip any field names not in CSV
            while field != field_list[0]:
                field_list.pop(0)
                field_map.pop(0)

            new_fieldnames.append(field_map.pop(0))
            field_list.pop(0)

        return new_fieldnames

    def read_csv(self, csv_filename):
        """
        Reads data from CSV file into list of dicts
        """
        with open(csv_filename, 'r') as csvfile:
            reader = csv.reader(csvfile)
            fieldnames = self.remap_fieldnames(next(reader))
            # print(fieldnames)

            self.data = [dict(zip(fieldnames, row)) for row in reader]

        self.input_csv_path = Path(csv_filename)

        # pprint(self.data)

    def write_csv(self, csv_output_filename=None):
        """
        Writes data to CSV file
        """
        if not csv_output_filename and self.input_csv_path:
            csv_output_filename = self.input_csv_path.with_stem(self.input_csv_path.stem + "_enhanced")

        with open(csv_output_filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(self.data[0].keys())
            # writer.writerows([record.values() for record in self.data])
            writer.writerows([record.values() for record in self.data])

    def set_coordinates(self):
        """
        Set [lat, long, elev] coordinates after repairing bad GPS data by
            1) discarding anything with a low or high satellite count
            2) calculating the median lat & long values
            3) discarding anything further than limits from any of the median coordinate values
            4) interpolating missing coordinates between known coordinates
        """

        # Only set Coordinates in records with a valid Sats value
        for index, record in enumerate(self.data, start=1):
            record['Index'] = index
            record['DateTime'] = datetime.strptime(f"{record['Date']} {record['Time']}", '%Y-%m-%d %H:%M:%S.%f')
            record["Sats"] = int(record["Sats"])
            record["GPS Alt(m)"] = float(record["GPS Alt(m)"])

            record["Coordinates"] = None
            if self.validSatRange[0] < record["Sats"] < self.validSatRange[1]:
                record["Coordinates"] = (
                        [float(ordinate) for ordinate in record["GPS"].split(' ')] + [record["GPS Alt(m)"]])

        median_coords = [
            np.median(np.array([record["Coordinates"][coord_index] for record in self.data if record["Coordinates"]]))
            for coord_index in range(3)]

        # print(median_coords)

        empty_start_index = 0  # First empty coordinate index in empty range
        empty_end_index = 0  # First non-empty coordinate index after empty range
        for index, record in enumerate(self.data):

            # Discard any coordinates outside the allowable range from the median
            if any([record['Coordinates'] and abs(record['Coordinates'][coord_index] - median_coords[coord_index]) >=
                    self.xyzLimit[coord_index] for coord_index in range(3)]):
                record['Coordinates'] = None

            if record['Coordinates'] is None:  # Invalid coordinates
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

                    # print(start_coords, end_coords)
                    for empty_index in range(empty_count):
                        self.data[empty_start_index + empty_index]['Coordinates'] = [round(start_coords[coord_index] + (
                                (end_coords[coord_index] - start_coords[coord_index]) * (empty_index + 1) / float(
                            empty_count + 1)), self.xyzRounding[coord_index]) for coord_index in range(3)]
                        self.data[empty_start_index + empty_index][
                            "Interpolated"] = True  # print(f'empty_index: {empty_index}, Coordinates: {self.data[empty_start_index + empty_index]['Coordinates']}')
                empty_start_index = 0
                empty_end_index = 0

            # print(f'index: {index}, Sats: {record["Sats"]}, Coordinates: {record["Coordinates"]}')

        self.coordinate_ranges = [[min([record['Coordinates'][coord_index] for record in self.data]),
                                   max([record['Coordinates'][coord_index] for record in self.data])] for coord_index in
                                  range(3)]  # print(self.coordinate_ranges)

    def write_kml(self, kml_output_filename=None):
        """
        Writes data to KML file
        """
        if not kml_output_filename and self.input_csv_path:
            kml_output_filename = self.input_csv_path.with_suffix(".kml")

        kml = simplekml.Kml()
        # Reverse order of lat & long and change elevation to altitude
        linestring = kml.newlinestring(name=str(self.input_csv_path.stem), description="Flight path",
                                       coords=[record['Coordinates'][1::-1] + [record['Coordinates'][2] - self.coordinate_ranges[2][0]] for record in
                                               self.data])

        # Create points
        for record in self.data:
            # Reverse order of lat & long and change elevation to altitude
            point = kml.newpoint(
                name=record["DateTime"].time().isoformat(),
                description='\n'.join([f'{field_name}: {record[field_name]}' for field_name in self.displayed_fields if record.get(field_name)]),
                coords=[record['Coordinates'][1::-1] + [record['Coordinates'][2] - self.coordinate_ranges[2][0]]]
            )
            point.style.iconstyle.scale = 0.5
            point.style.iconstyle.icon.href = 'http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png'

            point.style.labelstyle.color = simplekml.Color.blue
            point.style.labelstyle.scale = 0.5
            point.style.labelstyle.color = simplekml.Color.blue

        kml.save(kml_output_filename)


if __name__ == '__main__':
    csv_input_file = sys.argv[1]

    converter = Telemetry2kmlConverter()
    converter.read_csv(csv_input_file)

    converter.set_coordinates()
    # pprint([[record["Coordinates"], record["Interpolated"]] for record in converter.data])

    converter.write_csv()
    converter.write_kml()
