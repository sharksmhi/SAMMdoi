# Script for uploading data and generating DOI using Zenodo services
# Modified from Joan Sala Calero, Deltares, (https://github.com/switchonproject/sip-html5-data-upload).

import requests
import json
import os

class DOI:
    def __init__(self, files2push, directory, datasetName, logger=None):
        # Inputs
        self.dataset = datasetName
        self.zapi = "https://zenodo.org/api/deposit/depositions"
        self.logger = logger
        self.direc = directory
        self.files = files2push
        # Read token from disk
        with open(os.path.join(os.path.dirname(__file__), 'ztoken.txt')) as f:
            self.ztoken = f.read().strip()

    # Empty upload + get identifier
    def zenodoinitUpload(self):
        self.logger.info('DOI create:')
        data = {
            "metadata": {
                "title": self.dataset,
                "upload_type": "dataset",
                "description": "Water Switch-ON project dataset",
            }
        }
        return requests.post(self.zapi + "?access_token=" + self.ztoken, data=json.dumps(data), headers={"Content-Type": "application/json"})

    # Upload a single file smaller than 100mb
    def zenodoUploadFile(self, url_files, filepath):
        self.logger.info('DOI file upload:' + str(filepath))
        data = {'filename': os.path.basename(filepath) }
        files = {'file': open(filepath, 'rb')}
        return requests.post(url_files + "?access_token=" + self.ztoken, data=data, files=files, timeout=300)

    # Upload a single file bigger than 100mb
    def zenodoUploadFileBig(self, url_files, filepath):
        self.logger.info('DOI BIG file upload:' + str(filepath))
        return requests.put('%s/%s' % (url_files, os.path.basename(filepath)),
                         data=open(filepath, 'rb'),
                         headers={"Accept": "application/json",
                                  "Authorization": "Bearer %s" % self.ztoken,
                                  "Content-Type": "application/octet-stream"})

    # Is file bigger than the threshold of 100mb // Zenodo limitations
    def isFileBig(self, fname):
        szMB = os.path.getsize(fname) >> 20
        self.logger.info('The file upload size is:' + str(szMB))
        if szMB < 99:
            return False
        else:
            return True

    # Run the whole upload process
    def runUpload(self):
        # Empty upload + get identifier
        ret = self.zenodoinitUpload()
        res_create = ret.json()

        if ret.status_code < 300: # success
            # Data upload (file by file)
            for f in self.files:
                # Evaluate file size
                if self.isFileBig(os.path.join(self.direc, f)):
                    ret = self.zenodoUploadFileBig(res_create['links']['bucket'], os.path.join(self.direc, f))
                else:
                    ret = self.zenodoUploadFile(res_create['links']['files'], os.path.join(self.direc, f))
                if ret.status_code < 300:  # success
                    self.logger.info('OK, file uploaded on zenodo')
                else:
                    self.logger.error('ERR, uploading file via zenodo')
                    self.logger.error(ret)
        else:
            self.logger.error('ERR, preparing upload file via zenodo')

        return str(res_create['id'])
