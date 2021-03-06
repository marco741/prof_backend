from definitions.scraper import ScraperBase, ScrapeReply, DisamiguousLink
import requests
import requests
from bs4 import BeautifulSoup
import re
from grpclib.exceptions import GRPCError
from grpclib.const import Status


class ScraperWikipediaEN(ScraperBase):

    def _clean(self, text: str) -> str:
        """Function used to clean the string.

        Args:
            text (str): The string to clean
            
        Returns:
            str: The cleaned string
            """
        cleanstring = text
        cleanstring = re.sub('[A-Z]{1}?<=\.', '', cleanstring)
        cleanstring = re.sub('\.?=[A-Z]', '', cleanstring)
        cleanstring = re.sub('\[\d+\]', '', cleanstring)
        cleanstring = re.sub("\s?[\(\[].*?[\)\]]", "", text)
        cleanstring.replace(" .", ".")
        cleanstring.replace(" ,", ".")
        cleanstring = re.sub("\)", "", cleanstring)
        return cleanstring

    def _get_may_refer_to_list(self, soup: BeautifulSoup) -> list:
        """Function that manages the disambiguity pages

        Args:
            soup (BeautifulSoup): Parse tree used for analize the page
            
        Returns:
            list: The list of disambiguity
            """
        invalid_identifier = "action=edit"
        total = soup.find('div', {'class': 'mw-parser-output'})
        absolute_url = 'https://en.wikipedia.org'
        final_map = {}
        final_list = []
        for item in total.childGenerator():
            if item.name == "h2" and item.find('span', attrs={"id": ["See_also"]}):
                break
            if item.name == "ul":
                li = item.find_all('li')
                for l in li:
                    child = l.find("a")
                    if child is not None and not ((child.has_attr('class') and child['class'][0] == 'mw-disambig') or
                            ('wiktionary' in child['href'] and child['href'] is not None) or
                            child.has_attr('class') and child['class'][0] == 'mw-redirect'):
                        url = (absolute_url + child.get('href')) 
                        if invalid_identifier not in url:
                            final_map = DisamiguousLink(label=l.text, url=url)
                            final_list.append(final_map)
        if len(final_list) == 0:
            raise GRPCError(status=Status.NOT_FOUND, message="Text not found")
        return final_list

    def _create_soup(self, text: str) -> BeautifulSoup:
        """Function that creates the soup

        Args:
            text(str): the input string
            
        Raises:
            GRPCError: An exception to communicate the result not found error
            
        Returns:
            BeautifulSoup: The parse tree
            """
        if 'en.wikipedia.org' in text:
            endpoint = text
        else:
            text = text.strip().lower().split()
            prefix = f'https://en.wikipedia.org/wiki/'
            query = "_".join(text)
            processed_query = '_'.join(query.split())
            endpoint = f'{prefix}{processed_query}'
        req = requests.get(endpoint)
        if req.status_code != 200:
            raise GRPCError(status=Status.NOT_FOUND, message="Text not found")
        soup = BeautifulSoup(req.text, 'html.parser')
        return soup

    def _get_summary(self, soup: BeautifulSoup) -> str:
        """Commodity function used to obtain the summary

        Args:
            soup(BeautifulSoup): Parse tree used for analize the page
            
        Returns:
            str: the summary(first paragraph)
            """
        first_paragraph = soup.find('div', {'class': 'mw-parser-output'}).find_all('p', limit=5, recursive=False)
        summary = None
        for p in first_paragraph:
            if len(p.text) > 5:
                summary = self._clean(p.text)
                break
        return summary

    def _is_disambiguous(self, summary: str) -> bool:
        """Function used to chech if the page is a disasambiguity page

        Args:
            summary(str): the summary(first paragraph)
            
        Returns:
            bool: a value that establish the disambiguity
            """
        disambiguous_phrase = 'may refer to'
        return disambiguous_phrase in summary

    async def search(self, text: str) -> ScrapeReply:
        """The function for the short search

        Args:
            text(str): the input string
            
        Raises:
            GRPCError: An exception to communicate the result not found error
            
        Returns:
            ScrapeReply: The response of the service
            """
        soup = self._create_soup(text)
        summary = self._get_summary(soup)
        if summary is None:
            raise GRPCError(status=Status.NOT_FOUND, message="Summary not found")

        if self._is_disambiguous(summary):
            disambiguouslink = self._get_may_refer_to_list(soup)
            return ScrapeReply(language="en", disambiguous=True, disambiguous_data=disambiguouslink)
        return ScrapeReply(language="en", disambiguous=False, data=summary)

    async def long_search(self, text: str) -> ScrapeReply:
        """The function for the long search

        Args:
            text(str): the input string
        
        Raises:
            GRPCError: An exception to communicate the result not found error
            
        Returns:
            ScrapeReply: The response of the service
            """
        soup = self._create_soup(text)
        summary = self._get_summary(soup)
        if summary is None:
            raise GRPCError(status=Status.NOT_FOUND, message="Summary not found")

        if self._is_disambiguous(summary):
            disambiguouslink = self._get_may_refer_to_list(soup)
            return ScrapeReply(language="en", disambiguous=True, disambiguous_data=disambiguouslink)
        total = soup.find('div', {'class': 'mw-parser-output'})
        result = ""
        for t in total.childGenerator():
            if t.name == "h2" and t.find('span', attrs={"id": ["See_also"]}):
                break
            if t.name in ["p", "h2", "h3", "ul", "h4"]:
                result += t.get_text() + "\n\n"
        data = self._clean(result)
        return ScrapeReply(language="en", disambiguous=False, data=data)
