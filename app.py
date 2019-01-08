#!flask/bin/python
# Author: Markus Lindh
# Modified from: Johan Beekhuizen, Deltares, Joan Sala Calero, Deltares (https://github.com/switchonproject/sip-html5-data-upload)
# Their work is based on the Flask Upload Tool by Ngo Duy Khanh (https://github.com/ngoduykhanh/flask-file-uploader)
# which in turn is based on the jQuery-File-Upload (https://github.com/blueimp/jQuery-File-Upload/)

import os
import simplejson
from flask import Flask, request, render_template, session, redirect, url_for, flash, send_from_directory
from flask_bootstrap import Bootstrap
from werkzeug.utils import secure_filename
from lib.upload_file import uploadfile
import logging
from logging.handlers import RotatingFileHandler
import json
import zipfile
import time
import functions
import threddsclient
import requests
from requests.auth import HTTPBasicAuth
import urllib
import re
from unicodedata import normalize
import traceback
import xml.etree.ElementTree as ET
from geoserver.catalog import Catalog

# Specific from app
from DOI import DOI
from settings import settings

# used for 'slugify': creating a valid url
_punct_re = re.compile(r'[\t !"#$%&\'()*\-/<=>?@\[\\\]^_`{|},.]+')

app = Flask(__name__)
my_dir = os.path.dirname(__file__)
app.config.update(settings)

bootstrap = Bootstrap(app)

# set up logging
logFile = os.path.join(my_dir, 'datauploadtool.log')
file_handler = RotatingFileHandler(logFile, 'a', 1 * 1024 * 1024, 10)
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
app.logger.setLevel(logging.INFO)
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.info('Data Upload Tool startup')


@app.errorhandler(500)
def internal_server_error(error):
    app.logger.error('Server Error: %s', (error))
    app.logger.error(traceback.format_exc())
    return render_template('500.html'), 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    app.logger.error('Unhandled Exception: %s', (e))
    app.logger.error(traceback.format_exc())
    return render_template('500.html'), 500

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_THREDDS_EXTENSIONS']


def slugify(text, delim=u'_'):
    """Generates an ASCII-only slug."""
    result = []
    for word in _punct_re.split(text.lower()):
        word = normalize('NFKD', word).encode('ascii', 'ignore')
        if word:
            result.append(word)
    return unicode(delim.join(result))


def gen_file_name(fullpath, filename):
    """
    If file exist already, rename it and return a new name
    """
    i = 1
    while os.path.exists(os.path.join(fullpath, filename)):
        name, extension = os.path.splitext(filename)
        filename = '%s_%s%s' % (name, str(i), extension)
        i = i + 1

    return filename


def checkConnection(url, errorMessage):
    """
    This function checks if a server is responsive
    :param url: the url of the connection to check
    :param errorMessage: the errormessage to log when the connection fails
    :return:
    """

    try:
        requests.get(url, timeout=1)  # try for max 1 second
    except:
        flash(errorMessage)
        app.logger.error(errorMessage)
        return False

    return True


@app.route("/zip", methods=['POST'])
def zip():
    """
    Zip all the selected files in the list of uploaded files
    :return:
    """

    jsonString = request.data
    jsonDict = json.loads(jsonString)

    filesDict = jsonDict['files']
    zipFilename = jsonDict['zipfilename']

    datasetFoldername = session['DATASETFOLDERNAME']

    # create list of the file names from dict
    fileList = []
    for key in filesDict.keys():
        fileList.append(filesDict[key].split('/')[-1])

    if len(fileList) > 0:

        # do not zip an already zipped file
        if len(fileList) == 1:
            filename, fileExtension = os.path.splitext(fileList[0])

            if fileExtension == '.zip':
                flash("Cannot zip a .zip file.")
                return simplejson.dumps({"Error": "Cannot zip a .zip file."})

        datasetDir = '/'.join([app.config['BASE_UPLOAD_FOLDER'], datasetFoldername])

        # Open a zip file
        zipPath = os.path.join(datasetDir, "{}.zip".format(zipFilename))

        # check if the file already exists; if not, create zipfile
        if os.path.isfile(zipPath):
            flash("File already exists, please give a different file name.")
            return simplejson.dumps({"Error": "File already exists, please give a different file name."})
        else:
            zf = zipfile.ZipFile(zipPath, 'w')

        # write all selected files to the zip file
        for file in fileList:
            filePath = '/'.join([datasetDir, file])
            zf.write(filePath, file)

        zf.close()

        # delete all the original files
        for file in fileList:
            filePath = '/'.join([datasetDir, file])
            os.remove(filePath)

        return simplejson.dumps({"files": filesDict})

    else:
        flash("No files selected. Please select the files to zip using the checkboxes on the right.")
        return simplejson.dumps({"Error": "No file selected"})


@app.route("/submitfiles", methods=['GET', 'POST'])
def submitFiles():
    """
    Send the information of the uploaded files to the Open Data Registration Tool as an encoded JSON string in a GET-request
    The info is stored in a list of representations, according to the Open Data Registration Tool API:
    https://github.com/switchonproject/sip-html5-resource-registration/wiki
    """

    threddsAvailable = True
    geoserverAvailable = True

    # Check if Thredds server is online
    threddsAvailable = checkConnection(app.config['THREDDS_SERVER'],
                        "Failed to connect to the THREDDS server at " + app.config['THREDDS_SERVER'] + \
                                ". NetCDF files will not be accessible using web services, only by HTTP download.")

    # Check if GeoServer is online
    geoserverAvailable = checkConnection(app.config['GEOSERVER'],
                        "Failed to connect to the geoserver at " + app.config['GEOSERVER'] + \
                                ". Shapefiles will not be mapped with WMS and can not be downloaded by WFS.")

    datasetname = session['DATASETNAME']
    datasetFoldername = session['DATASETFOLDERNAME']
    generateDOI = session['GENERATEDOI']

    if request.form['submitButton'] == 'previous':
        return redirect('/?datasetname=' + datasetFoldername)

    if request.form['submitButton'] == 'next':

        datasetDir = os.path.join(app.config['BASE_UPLOAD_FOLDER'], datasetFoldername)

        files = [f for f in os.listdir(datasetDir) if
                 os.path.isfile(os.path.join(datasetDir, f)) and f not in app.config['IGNORED_FILES']]

        if len(files) > 0:

            representation = {}
            result = []
            urlRoot = request.url_root.rstrip('/')  # get the url root without the traling '/' (for string concatenation)

            # Store the root url of the dataset as the primary representation if there are more than 1 file
            if len(files) > 1:
                representation['name'] = datasetname
                representation['description'] = "File download"
                representation['type'] = "original data"
                representation['contentlocation'] = '/'.join([urlRoot, 'data', datasetFoldername])
                representation['contenttype'] = "application/octet-stream"
                representation['function'] = "information"
                representation['protocol'] = "WWW:LINK-1.0-http--link"
                result.append(representation)

            # if there is only one file, store the direct link to this file
            if len(files) == 1:
                filename, fileExtension = os.path.splitext(f)

                # region Check if it is a zipped shapefile
                # if it is, ignore it (unless geoserver is unavailable), otherwise the zip file is added twice
                zippedShapefile = False

                if fileExtension == '.zip' and geoserverAvailable:
                    zipFilePath =  os.path.join(datasetDir, f)
                    zipFile = zipfile.ZipFile(zipFilePath, 'r')
                    filesInZip = zipFile.namelist()
                    zipFile.close()

                    for fileInZip in filesInZip:
                        fileInZipExtension = os.path.splitext(fileInZip)[1]
                        if fileInZipExtension == '.shp':
                            zippedShapefile = True
                #endregion

                if fileExtension != '.nc' and zippedShapefile == False:
                    representation['name'] = datasetname
                    representation['description'] = "File download"
                    representation['type'] = "original data"

                    # TODO: improve file recognition
                    if fileExtension == ".zip":
                        representation['contenttype'] = "application/zip"
                    else:
                        representation['contenttype'] = "application/octet-stream"

                    representation['contentlocation'] = '/'.join([urlRoot, 'data', datasetFoldername, f])
                    representation['function'] = "download"
                    representation['protocol'] = "WWW:DOWNLOAD-1.0-http--download"
                    result.append(representation)


            #region THREDDS
            if threddsAvailable:
                if app.config['DEVELOP']:
                    threddsCatalog = '/'.join((app.config['THREDDS_SERVER'], 'netcdftest', 'catalog.xml'))
                else:
                    threddsCatalog = '/'.join((app.config['THREDDS_SERVER'], datasetFoldername, 'catalog.xml'))

                try:
                    opendapUrls = threddsclient.opendap_urls(threddsCatalog)

                    for opendapUrl in opendapUrls:

                        filepath, fileExtension = os.path.splitext(opendapUrl)
                        filename = opendapUrl.split('/')[-1]

                        # check if the file is a netCDF file; if yes, store OPeNDAP service url and html download url
                        if fileExtension == '.nc':
                            representation = {}

                            representation['name'] = filename
                            representation['description'] = "Netcdf file OPeNDAP service"
                            representation['contentlocation'] = opendapUrl
                            representation['contenttype'] = "application/x-netcdf"
                            representation['type'] = "original data"
                            representation['function'] = "service"
                            representation['protocol'] = 'OPeNDAP:OPeNDAP'
                            result.append(representation)

                            representation = {}
                            representation['name'] = filename
                            representation['description'] = "HTML interface OPeNDAP service"
                            representation['contentlocation'] = opendapUrl + ".html"
                            representation['contenttype'] = "application/x-netcdf"
                            representation['type'] = "original data"
                            representation['function'] = "download"
                            representation['protocol'] = 'WWW:DOWNLOAD-1.0-http--download'
                            result.append(representation)

                            representation = {}
                            representation['name'] = filename
                            representation['description'] = "WMS service"
                            representation['contentlocation'] = opendapUrl.replace('dodsC', 'wms') + "?service=WMS&version=1.3.0&request=GetCapabilities"
                            representation['contenttype'] = "application/xml"
                            representation['type'] = "original data"
                            representation['function'] = "service"
                            representation['protocol'] = 'OGC:WMS-1.1.1-http-get-capabilities'
                            result.append(representation)
                except:
                    app.logger.info("URL: " + threddsCatalog + " is not a THREDDS catalog")
            #endregion


            # region GEOSERVER: loop through all files to check for shapefiles
            if geoserverAvailable:
                for file in files:

                    layerName = ''
                    filename, fileExtension = os.path.splitext(file)

                    if fileExtension == '.zip':

                        zipFilePath =  os.path.join(datasetDir, file)
                        zipFile = zipfile.ZipFile(zipFilePath, 'r')
                        filesInZip = zipFile.namelist()

                        for fileInZip in filesInZip:
                            fileInZipName = os.path.split(fileInZip)[1]
                            fileInZipNoExtName, fileInZipExtension = os.path.splitext(fileInZipName)

                            if fileInZipExtension == '.shp':
                                # Layer name is the file without extension
                                layerName = fileInZipNoExtName

                                # Publish .zipped shapefile on geoserver, no subdirectories
                                zipFile.extractall(datasetDir)
                                for root, dirs, files in os.walk(datasetDir):
                                    for name in files:
                                        os.rename(os.path.join(root, name), os.path.join(datasetDir,name))

                                # create workspace
                                r = requests.post(url= app.config['GEOSERVER'] + "/rest/workspaces",
                                                 headers={'Content-type':  'text/xml'},
                                                 data="<workspace><name>" + datasetFoldername + "</name></workspace>",
                                                 auth=HTTPBasicAuth(app.config['GEOSERVER_ADMIN'], app.config['GEOSERVER_PASS']))

                                if r.status_code > 299:    # status code of 201 is success; all else is failure
                                    app.logger.error("Error in creating geoserver workspace for " + datasetFoldername + \
                                                     "; Status code: " + str(r.status_code) + ", Content: " + r.content)
                                    flash("Error in creating workspace on geoserver.")
                                    return redirect(url_for('uploadData'))

                                # for testing purposes.. uploaded file is on local machine and can only publish data that is on the data mount of web app
                                if app.config['DEVELOP']:
                                   shapeFile = "file://D:/sala/Downloads/sld_cookbook_polygon/sld_cookbook_polygon.shp"
                                else:
                                   shapeFile = settings['GEOSERVER_DATA_DIR'] + "/" + datasetFoldername + "/" + fileInZipName

                                # Publish shapefile on the geoserver; the datastore is automatically created and has the same name as the shapefile + ds
                                r = requests.put(url=app.config['GEOSERVER'] + "/rest/workspaces/" + datasetFoldername + "/datastores/" + datasetFoldername + "_ds/external.shp",
                                                 headers={'Content-type': 'text/plain'},
                                                 data='file://'+shapeFile,
                                                 auth=HTTPBasicAuth(app.config['GEOSERVER_ADMIN'], app.config['GEOSERVER_PASS']))

                                if r.status_code > 299:
                                    app.logger.error("Error in publishing shapefile " + datasetFoldername + " on geoserver; Status code: " \
                                                     + str(r.status_code) + ", Content: " + r.content)
                                    flash("Error in publishing shapefile on geoserver.")
                                    return redirect(url_for('uploadData'))

                                representation = {}
                                representation['name'] = layerName
                                representation['description'] = "WMS service"
                                representation['contentlocation'] = app.config['GEOSERVER'] + "/" + datasetFoldername + "/" + \
                                                                    "wms?service=WMS&version=1.1.0&request=GetCapabilities"
                                representation['contenttype'] = "application/xml"
                                representation['type'] = "original data"
                                representation['function'] = "service"
                                representation['protocol'] = 'OGC:WMS-1.1.1-http-get-capabilities'
                                result.append(representation)

                                representation = {}
                                representation['name'] = layerName
                                representation['description'] = "WMS service"
                                representation['contentlocation'] = app.config['GEOSERVER'] + "/" + datasetFoldername + "/" + \
                                                                    "wms?service=WMS&version=1.1.0&request=GetCapabilities"
                                representation['contenttype'] = "application/xml"
                                representation['type'] = "aggregated data"
                                representation['function'] = "service"
                                representation['protocol'] = 'OGC:WMS-1.1.1-http-get-capabilities'

                                #region Get spatial extent from getcapabilities document
                                try:
                                    root = ET.fromstring(requests.get(representation['contentlocation']).content)
                                    latlonElem = root.find('Capability/Layer/Layer/LatLonBoundingBox')
                                    latlonDict = latlonElem.attrib

                                    minx = latlonDict['minx']
                                    miny = latlonDict['miny']
                                    maxx = latlonDict['maxx']
                                    maxy = latlonDict['maxy']

                                    # WKT representation: POLYGON((minx miny, maxx miny, maxx maxy, minx maxy, minx miny))
                                    WKTString = 'POLYGON(({0} {1}, {2} {1}, {2} {3}, {0} {3}, {0} {1}))'.format(minx, miny, maxx, maxy)
                                    representation['wktboundingbox'] = WKTString
                                except:
                                    app.logger.error("Error in deriving WKT bounding box from WMS getcapabilities document")
                                #endregion

                                result.append(representation)

                                representation = {}
                                representation['name'] = fileInZipNoExtName
                                representation['description'] = "WFS service"
                                representation['contentlocation'] = app.config['GEOSERVER'] + "/" + datasetFoldername + "/" + "ows?service=WFS&version=1.0.0&request=GetCapabilities"
                                representation['contenttype'] = "application/xml"
                                representation['type'] = "original data"
                                representation['function'] = "service"
                                representation['protocol'] = "OGC:WFS-1.0.0-http-get-capabilities"
                                result.append(representation)

                                representation = {}
                                representation['name'] = file
                                representation['description'] = "Zipped shapefile"
                                representation['contentlocation'] = '/'.join([urlRoot, 'data', datasetFoldername, file])
                                representation['contenttype'] = "application/zip"
                                representation['type'] = "original data"
                                representation['function'] = "download"
                                representation['protocol'] = "WWW:DOWNLOAD-1.0-http--download"
                                representation['uploadmessage'] = "deriveSpatialIndex:shp"
                                result.append(representation)

                        # Optional sld file (preconditions, shp uploaded, workspace created)
                        for fileInZip in filesInZip:
                            fileInZipName = os.path.split(fileInZip)[1]
                            fileInZipNoExtName, fileInZipExtension = os.path.splitext(fileInZipName)

                            if fileInZipExtension == '.sld':
                                # for testing purposes.. uploaded file is on local machine and can only publish data that is on the data mount of web app
                                if app.config['DEVELOP']:
                                    sldFile = "D:/sala/Downloads/sld_cookbook_polygon/sld_cookbook_polygon.sld"
                                else:
                                    sldFile = settings['GEOSERVER_DATA_DIR'] + "/" + datasetFoldername + "/" + fileInZipName

                                # Connect to geoserver catalogue
                                cat = Catalog(app.config['GEOSERVER'] + "/rest", app.config['GEOSERVER_ADMIN'], password=app.config['GEOSERVER_PASS'])

                                # Add or Overwrite
                                with open(sldFile) as f:
                                    style=cat.create_style(fileInZipNoExtName, f.read(), overwrite=True)
                                # Link it to the layer
                                layer = cat.get_layer(layerName)
                                layer._set_default_style(fileInZipNoExtName)
                                cat.save(layer)

                        # close zip file after looping through all files in the zip file
                        zipFile.close()
            #endregion

            # region
            if generateDOI:
                d = DOI(files, datasetDir, datasetname, logger=app.logger)
                deposition_id = d.runUpload()

            # endregion
            resultString = json.dumps(result)
            text = urllib.quote_plus(resultString.encode('utf-8'))
            if generateDOI:     url = app.config['METADATA_URL'] + text + '&deposition=' + deposition_id
            else:               url = app.config['METADATA_URL'] + text

            # store the representation
            app.logger.info("Representations of the dataset: " + resultString)
            return redirect(url)
        else:
            flash("Please upload at least one file")
            return redirect(url_for('uploadData'))


# accessed from the 'selectServer' page
@app.route("/uploaddata", methods=['GET', 'POST'])
def uploadData():

    datasetname = session['DATASETNAME']
    datasetFoldername = session['DATASETFOLDERNAME']

    return render_template('upload.html', datasetname=datasetname, datasetFoldername=datasetFoldername)


@app.route("/upload", methods=['GET', 'POST'])
def upload():
    '''
    The upload function is called as an AJAX request from within the Upload.html page in order to avoid refreshing the whole page
    when uploading new data.
    '''

    if request.method == 'POST':

        file = request.files['file']
        datasetFoldername = session['DATASETFOLDERNAME']  # get the name of the dataset (and folder)
        fullpath = os.path.join(app.config['BASE_UPLOAD_FOLDER'], datasetFoldername)

        if file:

            filename = secure_filename(file.filename)
            filename = gen_file_name(fullpath, filename)

            try:
                uploaded_file_path = os.path.join(app.config['BASE_UPLOAD_FOLDER'], datasetFoldername, filename)
                file.save(uploaded_file_path)
                size = os.path.getsize(uploaded_file_path)  # get file size after saving
            except:
                errorMessage = 'Error saving file: ' + filename + ' to working copy'
                app.logger.error(errorMessage)
                return simplejson.dumps({"Error: ": errorMessage})

            app.logger.info('File: ' + filename + ' saved succesfully in working copy')
            time.sleep(0.2)
            result = uploadfile(name=filename, datasetFoldername=datasetFoldername, size=size)

            return simplejson.dumps({"files": [result.get_file()]})

    if request.method == 'GET':
        # get all file in ./data directory
        datasetFoldername = session['DATASETFOLDERNAME']
        datasetDir = os.path.join(app.config['BASE_UPLOAD_FOLDER'], datasetFoldername)

        # GET INFORMATION OF ALL CURRENT FILES IN DIRECTORY
        files = [f for f in os.listdir(datasetDir) if
                 os.path.isfile(os.path.join(datasetDir, f)) and f not in app.config['IGNORED_FILES']]

        file_display = []

        for file in files:
            size = os.path.getsize(os.path.join(datasetDir, file))
            file_saved = uploadfile(name=file, datasetFoldername=datasetFoldername, size=size)
            file_display.append(file_saved.get_file())

        return simplejson.dumps({"files": file_display})


## START APP --> http://127.0.0.1:5000/?datasetname=testJ1&generateDOI=true
@app.route('/', methods=['GET'])
def createDatasetFolder():

    # Get a DOI via zenodo (optional)
    generateDOI = request.args.get('generateDOI')
    if generateDOI == None:
        generateDOI = False
    else:
        if generateDOI == 'true':   generateDOI = True
        else:                       generateDOI = False

    # Datasetname (mandatory)
    datasetname = request.args.get('datasetname')
    if datasetname == None:
        return "Please send a GET request with a parameter datasetname"
    else:
        datasetFoldername = datasetname  # the dataset folder name must be unique and be allowed as an URL

        # create a valid datasetFoldername for use in an URL
        datasetFoldername = slugify(unicode(datasetFoldername))

        # create the dataset folder in the folder of the servertype; if name already taken, increment foldername
        fullpath = os.path.join(app.config['BASE_UPLOAD_FOLDER'], datasetFoldername)

        n = 1
        origDatasetFoldername = datasetFoldername
        while os.path.exists(fullpath):
            datasetFoldername = origDatasetFoldername + str(n)
            fullpath = os.path.join(app.config['BASE_UPLOAD_FOLDER'], datasetFoldername)
            n += 1

        os.makedirs(fullpath)
        app.logger.info('Dataset will be stored in: ' + fullpath)

        # set cookies (used for page refresh)
        session['DATASETNAME'] = datasetname
        session['DATASETFOLDERNAME'] = datasetFoldername
        session['GENERATEDOI'] = generateDOI

        return redirect(url_for('uploadData'))


@app.route("/data/<datasetFoldername>/")
def downloadDataset(datasetFoldername):
    result = {}
    result['datasetFoldername'] = datasetFoldername

    datasetDir = os.path.join(os.path.join(app.config['BASE_UPLOAD_FOLDER'], datasetFoldername))

    fileInfoList = []
    files = [f for f in os.listdir(datasetDir) if
             os.path.isfile(os.path.join(datasetDir, f)) and f not in app.config['IGNORED_FILES']]
    for f in files:
        fileInfo = {}
        fileInfo['size'] = os.path.getsize(os.path.join(datasetDir, f))
        fileInfo['sizeText'] = functions.formatFileSize(fileInfo['size'])
        fileInfo['url'] = os.path.join(request.base_url, f)
        fileInfo['name'] = f

        fileInfoList.append(fileInfo)

    result['files'] = fileInfoList

    return render_template('download.html', result=result)


@app.route("/data/<path:path>", methods=['GET'])
def downloadFile(path):
    return send_from_directory(os.path.join(app.config['BASE_UPLOAD_FOLDER']), filename=path)


@app.route("/downloadallzip/<path:path>", methods=['GET'])
def downloadallzip(path):
    return send_from_directory(os.path.join(app.config['BASE_UPLOAD_FOLDER']), filename=path)


@app.route("/downloadall", methods=['POST'])
def downloadAll():
    """
    Zips all files of the dataset and redirects the client to this .zip file to start the download
    :return:
    """
    datasetFoldername = request.form['datasetFoldername']
    zipFilename = "{}.zip".format(datasetFoldername)

    zipRootFolder = os.path.join(app.config['BASE_UPLOAD_FOLDER'], app.config['ZIP_DOWNLOAD_ALL_FOLDER'])
    if not os.path.exists(zipRootFolder):
        os.makedirs(zipRootFolder)
        app.logger.info("Created zip root folder at: " + zipRootFolder)

    zipFilepath = os.path.join(app.config['BASE_UPLOAD_FOLDER'], app.config['ZIP_DOWNLOAD_ALL_FOLDER'], zipFilename)

    # Test if zip file already exists; if yes, remove this .zip file
    if os.path.exists(zipFilepath):
        os.remove(zipFilepath)

    datasetDir = os.path.join(os.path.join(app.config['BASE_UPLOAD_FOLDER'], datasetFoldername))

    files = [f for f in os.listdir(datasetDir) if
             os.path.isfile(os.path.join(datasetDir, f)) and f not in app.config['IGNORED_FILES']]

    # Open a zip file
    zf = zipfile.ZipFile(zipFilepath, 'w')

    for f in files:
        filename = os.path.join(datasetDir, f)
        arcName = f
        zf.write(filename, arcName)

    zf.close()

    downloadPath = '/'.join(["downloadallzip", app.config['ZIP_DOWNLOAD_ALL_FOLDER'], zipFilename])
    return redirect(downloadPath)


if __name__ == '__main__':

    if app.config['DEVELOP']:
        app.run(debug=True)  # DEVELOPMENT
    else:
        app.run(host='0.0.0.0')  # SERVER
