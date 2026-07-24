"use client";

/**
 * 商品ファクトシート：確認可能な事実だけを、取得元つきで表示する。
 *
 * 予測値（返信率・成功率・利益率・適性点数・★）は表示しない。取得できていない項目は
 * 推測で埋めず「未取得」と出す。AI が生成した文章には「AI要約」バッジを付けて、
 * 取得した事実と混同させない。規制は該当を断定せず「確認が必要な項目」として出す。
 */
import { useEffect, useState } from "react";

import { fetchProductFacts, type ProductFacts, type FactItem } from "@/lib/api";

function SourceLine({ item }: { item: FactItem }) {
  if (!item.source_kind && !item.checked_at) return null;
  return (
    <span className="ml-2 text-[10px] text-slate-400">
      {item.source_kind && <>取得元: {item.source_kind}</>}
      {item.source_url && (
        <>
          {" "}
          <a
            href={item.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:underline"
          >
            ↗
          </a>
        </>
      )}
      {item.checked_at && <> ・ 最終確認 {item.checked_at.slice(0, 16).replace("T", " ")}</>}
    </span>
  );
}

function Row({ item }: { item: FactItem }) {
  const value = Array.isArray(item.value) ? item.value.join(" / ") : item.value;
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-2 border-b border-slate-100 py-1.5 text-sm last:border-0">
      <dt className="text-slate-500">{item.label}</dt>
      <dd className="text-slate-900">
        {item.ai_generated && (
          <span className="mr-1.5 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold text-violet-700">
            AI要約
          </span>
        )}
        {value ? (
          item.source_url && item.label.includes("サイト") ? (
            <a
              href={item.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-700 hover:underline"
            >
              {value} ↗
            </a>
          ) : (
            <span>{value}</span>
          )
        ) : (
          <span className="text-slate-400">{item.status}</span>
        )}
        {item.note && <span className="ml-2 text-[10px] text-amber-700">{item.note}</span>}
        <SourceLine item={item} />
      </dd>
    </div>
  );
}

function Section({ title, items }: { title: string; items: FactItem[] }) {
  return (
    <div className="mt-4">
      <h3 className="text-xs font-bold tracking-wide text-slate-500">{title}</h3>
      <dl className="mt-1">
        {items.map((it) => (
          <Row key={it.label} item={it} />
        ))}
      </dl>
    </div>
  );
}

export default function ProductFactsPanel({ projectId }: { projectId: number }) {
  const [data, setData] = useState<ProductFacts | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchProductFacts(projectId)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)));
    return () => {
      active = false;
    };
  }, [projectId]);

  if (error) {
    return <p className="text-sm text-slate-500">事実情報を取得できませんでした（{error}）</p>;
  }
  if (!data) {
    return <p className="text-sm text-slate-400">読み込み中…</p>;
  }

  return (
    <div>
      {/* 商品画像は事実（クラファン商品ページ由来） */}
      {data.product.image_url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={data.product.image_url}
          alt={data.product.items[0]?.value?.toString() ?? "商品画像"}
          className="max-h-56 w-auto rounded-md border border-slate-200 object-contain"
        />
      )}

      <Section title="商品" items={data.product.items} />
      <Section title="クラファン実績" items={data.funding.items} />
      <Section title="メーカー情報" items={data.maker.items} />

      {/* 日本市場確認：見つからない場合も「日本未発売」と断定しない */}
      <div className="mt-4">
        <h3 className="text-xs font-bold tracking-wide text-slate-500">
          日本市場確認
          {!data.japan_market.checked && (
            <span className="ml-2 font-normal text-slate-400">（未実施）</span>
          )}
        </h3>
        <dl className="mt-1">
          {data.japan_market.items.map((it) => (
            <Row key={it.label} item={it} />
          ))}
        </dl>
      </div>

      {/* 確認が必要な規制項目（該当の断定はしない・根拠を併記） */}
      <div className="mt-4">
        <h3 className="text-xs font-bold tracking-wide text-slate-500">
          確認が必要な規制項目
        </h3>
        {data.regulatory.items.length === 0 ? (
          <p className="mt-1 text-sm text-slate-400">
            商品ページ上に該当する記載は見つかりませんでした
          </p>
        ) : (
          <ul className="mt-1 space-y-1.5">
            {data.regulatory.items.map((r) => (
              <li
                key={r.item}
                className="rounded border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-sm text-amber-900"
              >
                <span className="font-semibold">{r.item}</span>：{r.message}
                <div className="mt-0.5 text-[11px] text-amber-800">
                  根拠（商品ページ上の記載）: {r.evidence_terms.join(" / ")}
                  {r.source_url && (
                    <>
                      {" "}
                      <a
                        href={r.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-700 hover:underline"
                      >
                        商品ページで確認 ↗
                      </a>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-1 text-[11px] text-slate-400">{data.regulatory.note}</p>
      </div>

      {/* メール探索の可否（点数ではなく具体的な理由） */}
      {!data.contact_search.eligible && data.contact_search.reasons.length > 0 && (
        <div className="mt-4 rounded border border-slate-200 bg-slate-50 p-3 text-xs">
          <p className="font-semibold text-slate-600">メール探索を実行しない理由</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-slate-600">
            {data.contact_search.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-3 text-[10px] text-slate-400">
        取得日時: {data.generated_at.slice(0, 16).replace("T", " ")}
      </p>
    </div>
  );
}
