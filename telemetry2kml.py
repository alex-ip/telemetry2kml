import copy
import csv
import glob
import os.path
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import simplekml
import yaml
from scipy.interpolate import PchipInterpolator


class Telemetry2kmlConverter(object):
    """
    Telemetry2kmlConverter class definition
    """

    @staticmethod
    def datetime_to_float(dt):
        return datetime.timestamp(dt)

    @staticmethod
    def float_to_datetime(fl):
        return datetime.fromtimestamp(fl)

    def __init__(self, settings_file=None, debug=False):
        """
        Constructor for Telemetry2kmlConverter class
        @param settings_file: Settings file. Default is 'telemetry2kml_settings.yml'
        @param debug: Boolean parameter used to turn debug output on/off
        """
        settings_file = settings_file or os.path.join(os.path.dirname(os.path.realpath(__file__)), 'telemetry2kml_settings.yml')
        self.input_csv_paths = []
        self.coordinate_ranges = None
        self.data = []
        with open(settings_file, 'r') as stream:
            self.settings = yaml.load(stream, Loader=yaml.Loader)
        # print(settings)

    def remap_fieldnames(self, fieldnames):
        """
        Remaps field names from CSV to position-sensitive non-duplicated names
        """
        new_fieldnames = []

        # print(field_list)
        # print(fieldnames)

        field_mappings = copy.deepcopy(self.settings['field_mappings'])

        # Scan field list in reverse because we should always have a GPS "Alt(m)" before the optional Vario "Alt(m)"
        for field in fieldnames[-1::-1]:
            # print(field)
            if field_mapping := field_mappings.get(field):
                new_fieldnames.insert(0, field_mapping.pop(-1))
            else:
                new_fieldnames.insert(0, field)

        return new_fieldnames

    def read_csv(self, csv_filenames):
        """
        Reads data from CSV file into list of dicts
        """
        self.data = []
        for csv_filename in csv_filenames:
            with open(csv_filename, 'r') as csvfile:
                reader = csv.reader(csvfile)
                fieldnames = self.remap_fieldnames(next(reader))
                # print(fieldnames)

                self.data += [dict(zip(fieldnames, row)) for row in reader]

            self.input_csv_paths.append(Path(csv_filename))

        assert len(self.data), 'No data found'
        print(f'{len(self.data)} points read from {len(self.input_csv_paths)} CSV file(s)')
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

    def clean_coordinates(self):
        """
        Clean [lat, long, elev] from bad GPS data by
            1) discarding anything with a low or high satellite count
            2) calculating the median lat & long values
            3) discarding anything further than limits from any of the median coordinate values
            4) discarding duplicate coordinates
            5) discarding any coordinates requiring an impossible speeed
        """
        # Only set Coordinates in records with a valid Sats value
        for index, record in enumerate(self.data, start=1):
            record['Index'] = index
            record['DateTime'] = datetime.strptime(f"{record['Date']} {record['Time']}", '%Y-%m-%d %H:%M:%S.%f')
            record["Sats"] = int(record["Sats"])
            record["Point Description"] = "Valid GPS"

            record["Coordinates"] = None

            if self.settings['validSatRange'][0] < record["Sats"] < self.settings['validSatRange'][1]:
                # Store coordinates in xyz (longitude, latitude elevation) order. Prefer Vario alt over GPS alt.
                record["Coordinates"] = (
                        [float(ordinate) for ordinate in record["GPS"].split(' ')[1::-1]] +
                        [float(record.get("Vario Alt(m)") or record.get("GPS Alt(m)"))]
                )
            else:
                record["Point Description"] = f"Bad Satellite count: {record['Sats']}"

        median_coords = [
            np.median(np.array([record["Coordinates"][coord_index] for record in self.data if record["Coordinates"]]))
            for coord_index in range(3)]

        # print(median_coords)

        last_good_coord_index = None
        for index, record in enumerate(self.data):

            # Discard any coordinates outside the allowable range from the median or any duplicates
            if record['Coordinates'] is not None:
                # Too far from median
                if any([abs(record['Coordinates'][coord_index] - median_coords[coord_index]) >=
                        self.settings['xyzLimit'][coord_index] for coord_index in
                        range(3)]):
                    record[
                        "Point Description"] = f"Too far from median location {[float(coord) for coord in record['Coordinates']]} (median = {[float(coord) for coord in median_coords]})"
                    record['Coordinates'] = None

                # Repeated XY coordinates
                elif (last_good_coord_index and self.data[last_good_coord_index]['Coordinates'][:2] ==
                      record['Coordinates'][:2]
                ):
                    record["Point Description"] = "Duplicate location"
                    record['Coordinates'] = None

                # Impossible speed
                elif (last_good_coord_index
                      # Point is impossibly far from last good one
                      # TODO: Deal with situation where first coordinates are bad and subsequent ones are good
                      and (any([abs(((record['Coordinates'][coord_index] -
                                      self.data[last_good_coord_index]['Coordinates'][
                                          coord_index]) /
                                     (record['DateTime'] - self.data[last_good_coord_index][
                                         'DateTime']).total_seconds()))
                                >= self.settings['xyzDeltaLimit'][coord_index]
                                for coord_index in range(3)]
                               )
                      )
                ):

                    # #TODO: Remove this debug print
                    # print(f"index: {index}' "
                    #       'Deltas: ',
                    #       [abs(record['Coordinates'][coord_index] -
                    #              self.data[last_good_coord_index]['Coordinates'][
                    #                  coord_index])
                    #        for coord_index in range(3)],
                    #       'delta t: ',
                    #       (record['DateTime'] - self.data[last_good_coord_index][
                    #           'DateTime']).total_seconds(),
                    #       'deltas/t: ',
                    #       [abs(((record['Coordinates'][coord_index] -
                    #              self.data[last_good_coord_index]['Coordinates'][
                    #                  coord_index]) /
                    #             (record['DateTime'] - self.data[last_good_coord_index][
                    #                 'DateTime']).total_seconds()))
                    #        for coord_index in range(3)],
                    #       'Boolean: ',
                    #       [abs(((record['Coordinates'][coord_index] -
                    #              self.data[last_good_coord_index]['Coordinates'][
                    #                  coord_index]) /
                    #             (record['DateTime'] - self.data[last_good_coord_index][
                    #                 'DateTime']).total_seconds()))
                    #        >= self.settings['xyzDeltaLimit'][coord_index]
                    #        for coord_index in range(3)]
                    #       )

                    record["Point Description"] = f"Impossible speed: {[abs(((record['Coordinates'][coord_index] -
                                                                              self.data[last_good_coord_index]['Coordinates'][
                                                                                  coord_index]) /
                                                                             (record['DateTime'] - self.data[last_good_coord_index][
                                                                                 'DateTime']).total_seconds()))
                                                                        for coord_index in range(3)]}"

                    record['Coordinates'] = None

                # Valid coordinate
                elif record['Coordinates'] is not None:
                    last_good_coord_index = index

            # print(record["Index"], record["Point Description"])

        # Discard invalid points at start and end
        while self.data[0]["Coordinates"] is None:
            self.data.pop(0)

        while self.data[-1]["Coordinates"] is None:
            self.data.pop(-1)

    def interpolate_coordinates(self):
        """
        Interpolate empty [lat, long, elev] coordinates from t
        This should be done after purging bad GPS data
        """
        for record in self.data:
            record["Interpolated"] = record['Coordinates'] is None

        txyz_good = np.vstack(
            [
                [self.datetime_to_float(record['DateTime'])] + record['Coordinates']
                for record in self.data
                if record['Coordinates']
            ]
        )

        # Define tx, ty, tz functions
        interp_functions = [
            PchipInterpolator(
                txyz_good[:, 0], # good times
                txyz_good[:, coord_index], # good x,y, or z coordinates
            )
            for coord_index in range(1, 4)
        ]

        # Define array of times with missing coordinates
        interp_times = np.array(
            [
                self.datetime_to_float(record['DateTime'])
                for record in self.data
                if not record['Coordinates']
            ]
        )

        # Define list of indices with missing coordinates
        interp_indices = [
            index
            for index, record in enumerate(self.data)
            if not record['Coordinates']
        ]

        interp_coords = np.array([interp_functions[coord_index](interp_times) for coord_index in range(3)])

        for index, interp_index in enumerate(interp_indices):
            # self.data[interp_index]['Coordinates'] = interp_coords[:,index].tolist()
            self.data[interp_index]['Coordinates'] = [
                round(interp_coords[:, index].tolist()[coord_index], self.settings["xyzRounding"][coord_index])
                for coord_index in range(3)
            ]

        self.set_calculated_values()

    def set_calculated_values(self):
        """
        Set calculated field values - call this after interpolation
        """
        # pprint([[record["Index"], record["Coordinates"], record["Interpolated"]] for record in self.data])
        assert any([record['Coordinates'] for record in self.data]), "No Valid GPS Coordinates found"

        self.coordinate_ranges = [[min([record['Coordinates'][coord_index] for record in self.data]),
                                   max([record['Coordinates'][coord_index] for record in self.data])]
                                  for coord_index in range(3)]

        # print(self.coordinate_ranges)

        # Add calculated field for "Height above Ground (m)"
        for record in self.data:
            record["Height above Ground (m)"] = record['Coordinates'][2] - self.coordinate_ranges[2][0]

    def write_kml(self, kml_output_filename=None):
        """
        Writes data to KML file
        """
        if not kml_output_filename and self.input_csv_paths:
            kml_output_filename = self.input_csv_paths[-1].with_suffix(".kml")

        kml = simplekml.Kml()

        # Create lines
        # Reverse order of lat & long and change elevation to altitude preferencing "Vario Alt(m)"
        linestring = kml.newlinestring(name=f'{self.input_csv_paths[-1].stem} Flight Path',
                                       # description=f'{self.input_csv_path.stem} Flight Path',
                                       coords=[record['Coordinates'][:2] + [record["Height above Ground (m)"]]
                                               for record in self.data
                                               ]
                                       )
        linestring.altitudemode = simplekml.AltitudeMode.relativetoground

        linestring.style.linestyle.color = self.settings['line_style']['color']
        linestring.style.linestyle.width = self.settings['line_style']['width']

        # Determine displayed fields which actually exist in this data
        displayed_fields = [field_name for field_name in self.settings['displayed_fields']
                            if field_name in self.data[0].keys()]

        # Create points
        for record in self.data:
            # Reverse order of lat & long and change elevation to altitude preferencing "Vario Alt(m)"
            point = kml.newpoint(
                name=(record["DateTime"].time().isoformat() if self.settings['point_style']['label_points'] else None),
                description='\n'.join(
                    [f'{field_name}: {record[field_name]}' for field_name in displayed_fields]),
                coords=[record['Coordinates'][:2] + [record["Height above Ground (m)"]]]
            )

            point.timestamp.when = record["DateTime"].isoformat()
            point.altitudemode = simplekml.AltitudeMode.relativetoground

            point.style.iconstyle.scale = self.settings['point_style']['icon_scale']
            point.style.iconstyle.color = (self.settings['point_style']['interp_icon_color']
                                           if record['Interpolated']
                                           else self.settings['point_style']['icon_color'])
            point.style.iconstyle.icon.href = self.settings['point_style']['icon_href']

            point.style.labelstyle.color = self.settings['point_style']['label_color']
            point.style.labelstyle.scale = self.settings['point_style']['label_scale']

        kml.save(kml_output_filename)


if __name__ == '__main__':
    csv_input_files = []
    csv_input_paths = sys.argv[1:]
    for csv_input_path in csv_input_paths:
        csv_input_files += glob.glob(csv_input_path)

    csv_input_files = sorted(csv_input_files)

    # print(f'Loading { csv_input_files}')

    converter = Telemetry2kmlConverter()
    converter.read_csv(csv_input_files)

    converter.clean_coordinates()

    # for record in converter.data:
    #     print(
    #         f'index: {record["Index"]}, Coordinates: {record["Coordinates"]}, Interpolated: {record.get("Interpolated")}')

    converter.interpolate_coordinates()

    # for record in converter.data:
    #     print(
    #         f'index: {record["Index"]}, Coordinates: {record["Coordinates"]}, Interpolated: {record.get("Interpolated")}')

    # converter.write_csv()
    converter.write_kml()
