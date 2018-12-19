# aquaboxDOI
Python script for creating DOI:s and uploading data to Zenodo services (datacite.org) developed by Sh at SMHI.

make_DOI is a data upload tool is build with Python Flask and jQuery-File-Upload, with multiple file selection and progress bars. The tool is based on the [flask-file-uploader] (https://github.com/ngoduykhanh/flask-file-uploader). All scripts are modified from Joan Sala Calero, Deltares; (https://github.com/switchonproject/sip-html5-data-upload)

# Installation instructions:
Install all packages in the requirements.txt.

Place the ztoken.txt file in the main folder in order to connect to Zenodo.

create your own settings.py file in the root directory containing the settings for your deployment.
