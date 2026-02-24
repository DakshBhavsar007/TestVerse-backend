import json
import traceback
from typing import List
import google.generativeai as genai
from ..config import get_settings
from ..models import TestResult

settings = get_settings()

def get_ai_recommendations(result: TestResult) -> List[str]:
    """Analyze the test result using Gemini and return exactly 3-5 specific recommendations."""
    if not settings.google_gemini_api_key:
        return ["Google Gemini API key not configured. Enable AI for detailed recommendations."]

    try:
        genai.configure(api_key=settings.google_gemini_api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')

        # Limit the data to avoid context limit issues and keep it concise for the prompt
        crawled_samples = [c.url for c in (result.pages_crawled or [])[:5]]
        
        prompt = f"""
        Act as an expert Web Developer and Quality Assurance engineer.
        Review the following diagnostic test results for a website ({result.url}) and provide 3 to 5 highly specific, actionable suggestions for improvement.
        Format your response as a strict JSON array of strings. Do not use markdown backticks in your output, just the JSON array.
        Examine metrics such as speed, uptime, SSL, broken links, images, JS errors, and mobile responsiveness. 
        WEIGHTING INSTRUCTIONS:
        - Weight UX and Accessibility concerns (Mobile responsiveness, broken links, missing images) as highest priority (40%). 
        - Weight Performance optimization (Speed, TTFB, load times) as secondary (30%).
        - Weight Security/Reliability (SSL, Uptime, JS Console Errors) as tertiary (30%).
        If the overall score is high, find edge-case optimizations based on these weights.
        
        Data context:
        Speed: {result.speed.model_dump() if result.speed else 'N/A'}
        Uptime: {result.uptime.model_dump() if result.uptime else 'N/A'}
        SSL: {result.ssl.model_dump() if result.ssl else 'N/A'}
        Broken Links Count: {result.broken_links.broken_count if result.broken_links else "0"} (out of {result.broken_links.total_links if result.broken_links else "0"})
        Missing Images Count: {result.missing_images.missing_count if result.missing_images else "0"}
        JS Error Count: {result.js_errors.error_count if result.js_errors else "0"}
        Mobile Meta Viewport: {result.mobile_responsiveness.has_viewport_meta if result.mobile_responsiveness else "Unknown"}
        Mobile Match CSS: {result.mobile_responsiveness.has_responsive_css if result.mobile_responsiveness else "Unknown"}
        Crawled Pages Sample: {crawled_samples}
        
        Provide the JSON array of 3 to 5 strings now.
        """

        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # Strip potential markdown formatting that models sometimes include
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
            
        suggestions = json.loads(text.strip())
        
        if isinstance(suggestions, list) and len(suggestions) > 0:
            return [str(s) for s in suggestions[:5]]
        else:
            return ["Review the test data strictly for any failing components.", "Consider manual audit on JS errors."]
            
    except Exception as e:
        print(f"Error fetching AI recommendations: {e}")
        traceback.print_exc()
        return ["Unable to generate AI recommendations at this time due to an unexpected error."]
