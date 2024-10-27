#!/usr/bin/env python3
import os
import re
import sys
from urllib.parse import urlparse
from lxml import etree as ET
import urllib
from urllib.request import urlopen
import glob
import shutil
from pathlib import Path
import subprocess
from PIL import Image, ImageFile
import time
from datetime import date
from dateutil.parser import parser

# Cached data (RSS feed XML)
CACHED_DATA = os.path.join(os.path.dirname(__file__), ".cached")
CACHED_MEDIA = os.path.join(CACHED_DATA, "media")

CACHED_RSS_PREFIX = "rssPage"
CACHED_RSS_PAGENO = "%04u"
CACHED_RSS_SUFFIX = ".xml"

CACHED_URL_MAP_PATH = os.path.join(CACHED_DATA, 'url_maps')

MMM_RSS_URL = "http://www.mrmoneymustache.com/feed/?order=ASC&paged=%d"

COVER_PATH = os.path.join(os.path.dirname(__file__), "Cover.png")

IMG_MAX_WIDTH_PX = 450
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Book data (use data here to construct ebook
BOOK_DATA = os.path.join(os.path.dirname(__file__), 
    "import_index.html_in_this_folder_in_calibre_to_create_ebook")
MEDIA = os.path.join(BOOK_DATA, "media")

class RSSParser(object):
    """Downloads (or reads from local file cache) RSS data of MMM feed"""
    
    def __init__(self, url, pageNo=None): 
        self.url = url # Confusing design - URL doubles as an actual URL or a cached local file
        self.pageNo = pageNo
              
        url = "file://" + self.url if Path(self.url).exists() else self.url
        print("Trying to open and parse RSS feed @ <" + url + ">...")
        doc = ET.parse(urlopen(url))
        self.root = doc.getroot()

        # Cache the page        
        if self.pageNo is not None:
           self.url = os.path.join(CACHED_DATA, CACHED_RSS_PREFIX + 
                CACHED_RSS_PAGENO % (self.pageNo, ) + CACHED_RSS_SUFFIX)
           ET.ElementTree(self.root).write(open(self.url, "wb"))
        
    def parse(self):   
        """Extract useful data from the RSS posting"""     
        for item in self.root.find('channel').findall('item'):
            title = item.find('title').text
            url = item.find('link').text            
            text = item.find('.//content:encoded', namespaces=self.root.nsmap).text
            date = item.find('pubDate').text
            author = item.find('.//dc:creator', namespaces=self.root.nsmap).text
            
            yield (
                title.encode('utf-8'), 
                text.encode('utf-8'), 
                url.encode('utf-8'),
                date.encode('utf-8'),
                author.encode('utf-8'))

def getCachedPostings():
    """Get a list of all the cached RSS data on disk"""
    filePaths = glob.glob(os.path.join(CACHED_DATA, 
        CACHED_RSS_PREFIX + '*' + CACHED_RSS_SUFFIX))
    filePaths.sort()
    return filePaths
            
def getLastPostPageNo():
    """Get the last RSS page number downloaded"""
    downloadedPages = getCachedPostings()
    if not downloadedPages or not len(downloadedPages):
        return 1 # Pages start at 1
    downloadedPages.sort()
    lastPage = downloadedPages[-1]
    
    return int(re.findall(os.path.join(CACHED_DATA, CACHED_RSS_PREFIX 
        + r'(\d+)' + CACHED_RSS_SUFFIX), lastPage)[0])
    

def getLatestRssDataFromMMM():
    """Download newest RSS pages - always redownloads last page as it may
        be updated"""
    if not os.path.isdir(CACHED_DATA):
        os.mkdir(CACHED_DATA)

    parsers = []
    pageNo = getLastPostPageNo()
    
    print("Downloading pages %d and newer" % (pageNo, ))
    
    while True:
        try:
            print(MMM_RSS_URL)
            parser = RSSParser(MMM_RSS_URL % (pageNo), pageNo)            
            parsers.append(parser)
            pageNo += 1
        except IOError as e:
            print(f'Failed to open last (end of detected RSS pages), error: {e}')
            break
            
    return parsers


def getRssData():
    """Gets a list of all RSS data from cache and downloads"""    
    parsers = []
    
    print("Parsing cached pages from disk")
    
    # First parse our cached pages
    for cachedPageFilePath in getCachedPostings():
         parsers.append(RSSParser(cachedPageFilePath)) # No page number necessary since cached
         
    parsers.extend(getLatestRssDataFromMMM())
    
    return parsers
    
class Post(object):
    """Once we have the RSS data and have started parsing it, we can break
        it down into posts"""
    next = 0

    def __init__(self, title, url, text, date, author, num=None):
        self.title = title
        self.text = text
        self.url = url
        self.date = date
        self.author = author
        
        if num is None:
            num = Post.next
            Post.next = Post.next + 1
            self.localUrl = 'p%04d.html' % (num, )

def createPostsFromRss(parsers):
    """Create a list of all the posts from the RSS data"""
    posts = []
    
    for parser in parsers:
        for (title, text, url, date, author) in parser.parse():
            posts.append(Post(title, url, text, date, author)      )

    return posts
    
                            
def getCachedUrlMaps():
    if not os.path.isdir(CACHED_DATA):
        os.mkdir(CACHED_DATA)
        
    if not os.path.isfile(CACHED_URL_MAP):
        return ({}, {})
    
    remoteToLocal, localToRemote = pickle.load(open(CACHED_URL_MAP, 'rb'))
     
    return (remoteToLocal, localToRemote)
    
def saveUrlMaps(remoteToLocal, localToRemote):
    if not os.path.isdir(CACHED_DATA):
        os.mkdir(CACHED_DATA)
        
    pickle.dump((remoteToLocal, localToRemote), open(CACHED_URL_MAP, 'wb'))
    
def rewritePostLinks(posts):
    """We do this once we have all the posts since sometimes MMM goes back
        and edits earlier posts to include a link to a later posting"""
        
    print("Rewriting post links...")

    # Generate mapping of web to local post urls.
    postWebToLocalURLDictionary = {}
    for post in posts:
        postWebToLocalURLDictionary[post.url] = post.localUrl

    for post in posts:
        for url in postWebToLocalURLDictionary.keys():
            regex = re.compile('<a\\s(.*href=\")%s(\".*)>(.*)<\/a>' % url.decode('utf-8'))
            text = post.text if isinstance(post.text, str) else post.text.decode('utf-8')
            post.text = regex.sub('<a \\1' + postWebToLocalURLDictionary[url] + '\\2>\\3</a>', text)

def rewriteImageLinks(posts):
    print("Rewriting image links...")

    if not os.path.isdir(CACHED_MEDIA):
        os.mkdir(CACHED_MEDIA)

    for post in posts:
        text = post.text if isinstance(post.text, str) else post.text.decode('utf-8')
        
        # Drop featured image. When preset it's usually just a duplicate of the top image in the post
        text = re.sub(r'<a class=\"featured_image_link\".*</a>', "", text)
        
        tree = ET.HTML(text)
        for image in tree.findall('.//img'):
            # Drop responsive images
            image.attrib.pop('srcset', None)

            imageurl = image.attrib["src"]

            # Skip images embedded in the html
            if imageurl.startswith("data:image"):
                continue

            urlParseResult = urlparse(imageurl)
            imageFilename = urlParseResult.path.replace("/", "", 1) .replace("/", "_") # Create name from full path to help avoid accidentally overriding images, wordpress image paths have date component

            # Only include images actually hosted on mrmoneymustache.com
            if urlParseResult.hostname != "www.mrmoneymustache.com":
                print(f"Not including image in article {post.title} hosted at {imageurl}")
                continue

            # Cache images
            cachedImagePath = os.path.join(CACHED_MEDIA, imageFilename)
            if not Path(cachedImagePath).exists():
                try:
                    urllib.request.urlretrieve(imageurl, cachedImagePath)

                    # Resize images to a max width of 800px to save space
                    try:
                        imageFile = Image.open(cachedImagePath)
                        imageFile.LOAD_TRUNCATED_IMAGES = True
                        if not imageFile.width <= 600:
                            aspectRatioChange = IMG_MAX_WIDTH_PX / imageFile.width
                            height = int(imageFile.height * aspectRatioChange)
                            newSize = (IMG_MAX_WIDTH_PX, height)
                            imageFile = imageFile.resize(newSize)

                        imageFile.save(cachedImagePath, optimize=True, quality=85)
                    except IOError as e:
                        print(f'Failed to open image at path {cachedImagePath} for resize, caching at original resolution, error: {e}')
                except Exception as e:
                    print(f"Caching image {imageurl} to {cachedImagePath} failed with exception {e}")

            outputImageAbsolutePath = os.path.join(MEDIA, imageFilename)
            outputImageRelativePath = os.path.relpath(outputImageAbsolutePath, BOOK_DATA)
            shutil.copyfile(cachedImagePath, outputImageAbsolutePath)
            image.attrib['src'] = outputImageRelativePath
        post.text = ET.tostring(tree, encoding='utf8')
    
def createBookData(posts):
    print("Creating book data...")
    
    shutil.copyfile(COVER_PATH, os.path.join(BOOK_DATA, 'Cover.png'))

    index = open(os.path.join(BOOK_DATA, 'index.html'), 'w')
    
    index.write(f'''<!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
    </head>
    <body>
      <h1>Financial Freedom Through Badassity</h1>
      <h2>by Mr. Money Mustache</h2>
      Generated {time.strftime("%B %d, %Y")}
      <br>
      <h3>Table of Contents</h2>
      <p style="text-indent:0pt">''')

    chapter = 0
    for post in posts:
        text = post.text if isinstance(post.text, str) else post.text.decode('utf-8')
        
        open(os.path.join(BOOK_DATA, post.localUrl), 'w').write(
            '<!DOCTYPE html>\n' + \
            '<html lang="en">\n' + \
                '<head>\n' + \
                    '<meta charset="UTF-8">\n' + \
                    '<title>' + post.title.decode('utf-8') + "</title>\n" + \
                '</head>\n' + \
                '<body>\n' + \
                    '<h1 class="chapter">' + post.title.decode('utf-8') + "</h1>\n" + \
                    '<h3>By ' + post.author.decode('utf-8') + "</h3>\n" + \
                    '<h4> ' + parser().parse(post.date.decode('utf-8')).strftime("%A %B %-d, %Y") + "</h4>\n" + \
                    text + \
                '</body>' + \
            '</html>')
        chapter += 1
        index.write(f'{chapter}. <a href=%s>%s</a><br/>\n' % (post.localUrl, post.title.decode('utf-8')))
        
    index.write('''     </p>
   </body>
</html>''')

def generateEbooks():
    print("Generating eBooks...")

    subprocess.run(["ebook-convert", "import_index.html_in_this_folder_in_calibre_to_create_ebook/index.html", "Ebooks/mmm.azw3", "--title", "Financial Freedom Through Badassity", "--authors", "Mr. Money Mustache", "--pubdate", f"{date.today()}", "--cover", "Cover.png"])
    subprocess.run(["ebook-convert", "import_index.html_in_this_folder_in_calibre_to_create_ebook/index.html", "Ebooks/mmm.epub", "--title", "Financial Freedom Through Badassity", "--authors", "Mr. Money Mustache", "--pubdate", f"{date.today()}", "--cover", "Cover.png"])
    subprocess.run(["ebook-convert", "import_index.html_in_this_folder_in_calibre_to_create_ebook/index.html", "Ebooks/mmm.mobi", "--title", "Financial Freedom Through Badassity", "--authors", "Mr. Money Mustache", "--pubdate", f"{date.today()}", "--cover", "Cover.png"])
    subprocess.run(["ebook-convert", "import_index.html_in_this_folder_in_calibre_to_create_ebook/index.html", "Ebooks/mmm.pdf", "--title", "Financial Freedom Through Badassity", "--authors", "Mr. Money Mustache", "--cover", "Cover.png"])

    print("Finished generating Ebooks")

def main():
    if os.path.isdir(BOOK_DATA):
        shutil.rmtree(BOOK_DATA)

    os.mkdir(BOOK_DATA)
    os.mkdir(MEDIA)

    parsers = getRssData()
    posts = createPostsFromRss(parsers)
    rewritePostLinks(posts)
    rewriteImageLinks(posts)
    createBookData(posts)
    generateEbooks()
            
if __name__=="__main__":
    main()