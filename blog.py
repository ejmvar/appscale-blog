# The MIT License
# 
# Copyright (c) 2008 William T. Katz
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to 
# deal in the Software without restriction, including without limitation 
# the rights to use, copy, modify, merge, publish, distribute, sublicense, 
# and/or sell copies of the Software, and to permit persons to whom the 
# Software is furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER 
# DEALINGS IN THE SOFTWARE.

"""A simple RESTful blog/homepage app for Google App Engine

This simple homepage application tries to follow the ideas put forth in the
book 'RESTful Web Services' by Leonard Richardson & Sam Ruby.  It follows a
Resource-Oriented Architecture where each URL specifies a resource that
accepts HTTP verbs.

Rather than create new URLs to handle web-based form submission of resources,
this app embeds form submissions through javascript.  The ability to send
HTTP verbs POST, PUT, and DELETE is delivered through javascript within the
GET responses.  In other words, a rich client gets transmitted with each GET.

This app's API should be reasonably clean and easily targeted by other 
clients, like a Flex app or a desktop program.
"""
__author__ = 'William T. Katz'

import datetime
import string
import re
import os
import cgi

import logging

from google.appengine.ext import webapp
from google.appengine.api import users
from google.appengine.ext import db

import restful
import authorized
import model
import view
import config

import legacy_aliases   # This can be either manually created or 
                        # autogenerated using the drupal_uploader utility

# Functions to generate permalinks depending on type of article
permalink_funcs = {
    'article': lambda title,date: get_friendly_url(title),
    'blog entry': lambda title,date: str(date.year) + "/" + \
                        str(date.month) + "/" + get_friendly_url(title)
}

# We allow a mapping from some old url pattern to the current query 
#  using a regex's matched string.
def legacy_id_mapping(path, legacy_program):
    if legacy_program and legacy_program == 'Drupal':
        url_match = re.match('node/(\d+)', path)
        if url_match:
            return db.Query(model.Article). \
                filter('legacy_id =', url_match.group(1)). \
                get()
    return None

# Module methods to handle incoming data
def get_datetime(time_string = None):
    if time_string:
        return datetime.datetime.strptime(time_string, '%Y-%m-%d %H:%M:%S')
    return datetime.datetime.now()

def get_format(format_string):
    if not format_string or format_string not in ['html', 'textile']:
        format_string = 'html'
    return format_string

def get_tags(tag_string):
    if tag_string:
        return [db.Category(s.strip()) for s in tag_string.split(",") 
                if s != '']
    return None
    
def get_friendly_url(title):
    return re.sub('-+', '-', 
                  re.sub('[^\w-]', '', 
                         re.sub('\s+', '-', title.strip())))

def get_html(body, markup_type):
    if markup_type == 'textile':
        from external.libs import textile
        return textile.textile(str(body))
    return body
    
def fill_optional_properties(obj, property_dict):
    for key, value in property_dict.items():
        if value and not key in obj.__dict__:
            setattr(obj, key, value)

def process_article_edit(handler, permalink):
    # For http PUT, the parameters are passed in URIencoded string in body
    body = handler.request.body
    params = cgi.parse_qs(body)
    for key,value in params.iteritems():
        params[key] = value[0]
    property_hash = restful.get_sent_properties(params.get,
        ['title',
         'body',
         ('format', get_format),
         ('updated', get_datetime),
         ('tags', get_tags),
         ('html', get_html, 'body', 'format')]
    )

    if property_hash:
        article = db.Query(model.Article).filter('permalink =', permalink).get()
        for key,value in property_hash.iteritems():
            setattr(article, key, value)
        article.put()
        restful.send_successful_response(handler, '/' + article.permalink)
        view.invalidate_cache()
    else:
        handler.error(400)

def process_article_submission(handler, article_type):
    property_hash = restful.get_sent_properties(handler.request.get, 
        ['title',
         'body',
         'legacy_id',
         ('format', get_format),
         ('published', get_datetime),
         ('updated', get_datetime),
         ('tags', get_tags),
         ('html', get_html, 'body', 'format'),
         ('permalink', permalink_funcs[article_type], 'title', 'published')])

    if property_hash:
        property_hash['format'] = 'html'   # We are converting everything to HTML
        property_hash['article_type'] = article_type
        article = model.Article(**property_hash)
        article.set_associated_data(
            {'relevant_links': handler.request.get('relevant_links'),       
             'amazon_items': handler.request.get('amazon_items')})
        article.put()
        restful.send_successful_response(handler, '/' + article.permalink)
        view.invalidate_cache()
    else:
        handler.error(400)

def process_comment_submission(handler, article):
    if not article:
        handler.error(404)
        return

    # Get and store some pieces of information from parent article.
    # TODO: See if this overhead can be avoided
    if not article.num_comments:
        article.num_comments = 1
    else:
        article.num_comments += 1
    article_key = article.put()

    property_hash = restful.get_sent_properties(handler.request.get, 
        ['name',
         'email',
         'homepage',
         'title',
         'body',
         'key',
         ('published', get_datetime)])

    # Generate a thread string.
    matchobj = re.match(r'[^#]+#comment-(\w+)', property_hash['key'])
    if matchobj:
        logging.debug("Comment has parent: %s", matchobj.group(1))
        comment_key = matchobj.group(1)
        thread_string = "%03d" % article.num_comments
    else:
        logging.debug("Comment is off main article")
        comment_key = None
        thread_string = "%03d" % article.num_comments

    comment = model.Comment(
        name = property_hash['name'],
        email = property_hash['email'],
        homepage = property_hash['homepage'],
        title = property_hash['title'],
        body = property_hash['body'],
        article = article_key,
        thread = thread_string)
    comment.put()
    restful.send_successful_response(handler, '/' + article.permalink)
    view.invalidate_cache()

def render_article(handler, article):
    if article:
        page = view.ViewPage()
        page.render(handler, { "two_columns": article.is_big(), 
                               "article": article })
    else:
        # This didn't fall into any of our pages or aliases.
        # Page not found.
        #   could do --> self.redirect('/404.html')
        handler.error(404)
        view.ViewPage(cache_time=36000). \
             render(handler, {'module_name': 'blog', 
                           'handler_name': 'notfound'})

class NotFoundHandler(webapp.RequestHandler):
    def get(self):
        self.error(404)
        view.ViewPage(cache_time=36000).render(self)

class UnauthorizedHandler(webapp.RequestHandler):
    def get(self):
        self.error(403)
        view.ViewPage(cache_time=36000).render(self)

class RootHandler(restful.Controller):
    def get(self):
        logging.debug("RootHandler#get")
        page = view.ViewPage()
        page.render_query(
            self, 'articles', 
            db.Query(model.Article). \
               filter('article_type =', 'blog entry').order('-published'))

    @authorized.role("admin")
    def post(self):
        logging.debug("RootHandler#post")
        process_article_submission(handler=self, article_type='article')

# Articles are off root url
class ArticleHandler(restful.Controller):
    def get(self, path):
        logging.debug("ArticleHandler#get on path (%s)", path)
        # Handle precomputed legacy aliases
        # TODO: Use hash for case-insensitive lookup
        for alias in legacy_aliases.redirects:
            if path.lower() == alias.lower():
                self.redirect(legacy_aliases.redirects[alias])
                return

        # This lets you map arbitrary URL patterns like /node/3
        #  to article properties, e.g. 3 -> legacy_id property
        article = legacy_id_mapping(path, config.blog["legacy_blog_software"])

        # Check undated pages
        if not article:
            article = db.Query(model.Article). \
                         filter('permalink =', path).get()

        render_article(self, article)

    @restful.methods_via_query_allowed    
    @authorized.role("user")
    def post(self, path):
        article = db.Query(model.Article).filter('permalink =', path).get()
        process_comment_submission(self, article)

    @authorized.role("admin")
    def put(self, path):
        logging.debug("ArticleHandler#put")
        process_article_edit(self, permalink = path)

    @authorized.role("admin")
    def delete(self, path):
        """
        By using DELETE on /article or /comment, you can delete the first 
         entity of the desired kind.
        This is useful for writing utilities like clear_datastore.py.  
        TODO - Once we write a DELETE for specific entities, it makse sense to 
         DRY this up and just require a utility to inquire which entities are 
         available and then call DELETE on each permalink.
        """
        # TODO: Add DELETE for articles off root like blog entry DELETE.
        model_class = path.lower()

        def delete_entity(query):
            targets = query.fetch(limit=1)
            if len(targets) > 0:
                permalink = targets[0].permalink
                logging.debug('Deleting %s %s', model_class, permalink)
                targets[0].delete()
                self.response.out.write('Deleted ' + permalink)
                view.invalidate_cache()
            else:
                self.error(404)

        if model_class == 'article':
            query = model.Article.all()
            delete_entity(query)
        elif model_class == 'comment':
            query = model.Comment.all()
            delete_entity(query)
        else:
            self.error(404)

# Blog entries are dated articles
class BlogEntryHandler(restful.Controller):
    def get(self, year, month, perm_stem):
        logging.debug("BlogEntryHandler#get for year %s, "
                      "month %s, and perm_link %s", 
                      year, month, perm_stem)
        article = db.Query(model.Article). \
                     filter('permalink =', 
                            year + '/' + month + '/' + perm_stem).get()

        render_article(self, article)

    @restful.methods_via_query_allowed    
    @authorized.role("user")
    def post(self, year, month, perm_stem):
        logging.debug("Adding comment for blog entry %s", self.request.path)
        permalink = year + '/' + month + '/' + perm_stem
        article = db.Query(model.Article). \
                     filter('permalink =', permalink).get()
        process_comment_submission(self, article)

    @authorized.role("admin")
    def put(self, year, month, perm_stem):
        permalink = year + '/' + month + '/' + perm_stem
        logging.debug("BlogEntryHandler#put")
        process_article_edit(handler = self, permalink = permalink)

    @authorized.role("admin")
    def delete(self, year, month, perm_stem):
        permalink = year + '/' + month + '/' + perm_stem
        logging.debug("Deleting blog entry %s", permalink)
        article = db.Query(model.Article). \
                     filter('permalink =', permalink).get()
        article.delete()
        view.invalidate_cache()
        restful.send_successful_response(self, "/")

class TagHandler(restful.Controller):
    def get(self, encoded_tag):
        tag =  re.sub('(%25|%)(\d\d)', 
                      lambda cmatch: chr(string.atoi(cmatch.group(2), 16)),                 
                      encoded_tag)   # No urllib.unquote in AppEngine?

        page = view.ViewPage()
        page.render_query(
            self, 'articles', 
            db.Query(model.Article).filter('tags =', tag).order('-published'), 
            {'tag': tag})

class SearchHandler(restful.Controller):
    def get(self):
        search_term = self.request.get("s")
        page = view.ViewPage()
        page.render_query(
            self, 'articles', 
            # model.Article.all().search(search_term).order('-published'), 
            db.Query(model.Article). \
               filter('tags =', search_term).order('-published'), 
            {'search_term': search_term})

class YearHandler(restful.Controller):
    def get(self, year):
        logging.debug("YearHandler#get for year %s", year)
        start_date = datetime.datetime(string.atoi(year), 1, 1)
        end_date = datetime.datetime(string.atoi(year), 12, 31, 23, 59, 59)
        page = view.ViewPage()
        page.render_query(
            self, 'articles', 
            db.Query(model.Article).order('-published'). \
               filter('published >=', start_date). \
               filter('published <=', end_date), 
            {'title': 'Articles for ' + year, 'year': year})

class MonthHandler(restful.Controller):
    def get(self, year, month):
        logging.debug("MonthHandler#get for year %s, month %s", year, month)
        start_date = datetime.datetime(string.atoi(year), 
                                       string.atoi(month), 1)
        end_date = datetime.datetime(string.atoi(year), 
                                     string.atoi(month), 31, 23, 59, 59)
        page = view.ViewPage()
        page.render_query(
            self, 'articles', 
            db.Query(model.Article).order('-published'). \
               filter('published >=', start_date). \
               filter('published <=', end_date), 
            {'title': 'Articles for ' + month + '/' + year, 
             'year': year, 'month': month})

    @authorized.role("admin")
    def post(self, year, month):
        """ Add a blog entry. Since we are POSTing, the server handles 
            creation of the permalink url. """
        logging.debug("MonthHandler#post on date %s, %s", year, month)
        process_article_submission(handler=self, article_type='blog entry')
        
class AtomHandler(webapp.RequestHandler):
    def get(self):
        logging.debug("Sending Atom feed")
        articles = db.Query(model.Article). \
                      filter('article_type =', 'blog entry'). \
                      order('-published').fetch(limit=10)
        updated = ''
        if articles:
            updated = articles[0].rfc3339_updated()
        
        self.response.headers['Content-Type'] = 'application/atom+xml'
        page = view.ViewPage()
        page.render(self, {"blog_updated_timestamp": updated, 
                           "articles": articles, "ext": "xml"})
