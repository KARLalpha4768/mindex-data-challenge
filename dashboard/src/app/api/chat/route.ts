import { GoogleGenAI } from '@google/genai';
import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const README_CONTEXT = `
MINDEX Data Engineer Code Challenge - Pipeline & Architecture Summary
- 17 Defect Classes Reconciled (ST-01 to ST-03, PR-01 to PR-04, TX-01 to TX-10)
- Net Revenue: $158,044.29 ($0.00 Reconciliation Delta)
- Gross Sales: $168,957.80 | Returns: -$9,952.03 | Silent Discounts: $961.48
- Fact Table: 474 valid sales rows, 38 quarantined records in audit ledger
- Database: SQLite Star Schema Warehouse (warehouse.db)
`;

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

export async function POST(req: Request) {
  try {
    const { message } = await req.json();

    const systemInstruction = `You are an expert technical assistant representing Karl David's MINDEX Data Engineer Code Challenge submission.
Your answers MUST be strictly based on the technical details, architecture decisions, defect catalogs, and metric definitions provided.

<README_DOCUMENT>
${README_CONTEXT}
</README_DOCUMENT>`;

    const streamResult = await ai.models.generateContentStream({
      model: 'gemini-2.5-flash',
      contents: message,
      config: {
        systemInstruction,
        temperature: 0.2,
      },
    });

    const encoder = new TextEncoder();
    const readableStream = new ReadableStream({
      async start(controller) {
        for await (const chunk of streamResult) {
          if (chunk.text) {
            controller.enqueue(encoder.encode(chunk.text));
          }
        }
        controller.close();
      },
    });

    return new Response(readableStream, {
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'Transfer-Encoding': 'chunked',
      },
    });
  } catch (error) {
    console.error('Chatbot Streaming API Error:', error);
    return NextResponse.json({ error: 'Failed to generate response' }, { status: 500 });
  }
}